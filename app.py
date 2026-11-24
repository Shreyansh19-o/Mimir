
import datetime
import json
import os
import uuid
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from lg_db_be import chatbot, retrieve_all_threads, delete_thread
from tools import my_tools


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB


# --------------------------------------------------
# PDF INGESTION
# --------------------------------------------------

ingest_pdf = getattr(my_tools, "ingest_pdf", None)
get_pdf_info = getattr(my_tools, "get_pdf_info", lambda thread_id: {})


# --------------------------------------------------
# CONTENT HELPERS
# --------------------------------------------------

def displayable_content(content):
    """
    Convert LangChain message content into displayable text.

    Supports:
    - Normal string content
    - Multimodal list content
    - Text blocks
    """

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for block in content:

            if isinstance(block, str):
                parts.append(block)

            elif isinstance(block, dict):

                if block.get("type") == "text":
                    value = block.get("text", "")

                    if isinstance(value, str):
                        parts.append(value)

        return "\n".join(parts)

    return ""


def get_message_role(message):

    if isinstance(message, HumanMessage):
        return "user"

    if isinstance(message, AIMessage):
        return "assistant"

    return None


# --------------------------------------------------
# THREAD HELPERS
# --------------------------------------------------

def get_threads():

    try:
        threads = retrieve_all_threads() or []

        return [str(thread) for thread in threads]

    except Exception as exc:
        app.logger.exception("Could not retrieve threads: %s", exc)
        return []


def serialize_history(thread_id):

    config = {
        "configurable": {
            "thread_id": str(thread_id)
        }
    }

    state = chatbot.get_state(config)

    if not state or not state.values:
        return []

    messages = state.values.get("messages", [])

    result = []

    for message in messages:

        role = get_message_role(message)

        # Hide tool messages and other internal messages.
        if role is None:
            continue

        content = displayable_content(message.content)

        if content.strip():

            result.append({
                "role": role,
                "content": content
            })

    return result


# --------------------------------------------------
# FRONTEND
# --------------------------------------------------

@app.get("/")
def index():

    return render_template("index.html")


# --------------------------------------------------
# THREAD API
# --------------------------------------------------

@app.get("/api/threads")
def api_threads():

    return jsonify({
        "threads": get_threads()
    })


@app.post("/api/threads")
def create_thread():

    thread_id = (
        f"Chat-{datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}"
        f"-{uuid.uuid4().hex[:8]}"
    )

    return jsonify({
        "thread_id": thread_id,
        "messages": []
    })


@app.get("/api/history/<path:thread_id>")
def api_history(thread_id):

    try:

        messages = serialize_history(thread_id)

        return jsonify({
            "thread_id": thread_id,
            "messages": messages
        })

    except Exception as exc:

        app.logger.exception("History loading failed")

        return jsonify({
            "error": f"Could not load conversation: {str(exc)}"
        }), 500


# --------------------------------------------------
# PDF STATUS API
# --------------------------------------------------

@app.get("/api/pdf/<path:thread_id>")
def api_pdf_info(thread_id):

    info = get_pdf_info(thread_id) or {}

    return jsonify({
        "indexed": bool(info),
        "filename": info.get("filename"),
        "chunks": info.get("chunks", 0),
        "documents": info.get("documents", 0)
    })


# --------------------------------------------------
# PDF UPLOAD API
# --------------------------------------------------

@app.post("/api/upload")
def upload_pdf():

    thread_id = request.form.get("thread_id", "").strip()

    uploaded = request.files.get("file")

    if not thread_id:

        return jsonify({
            "error": "Missing conversation thread ID."
        }), 400

    if uploaded is None or not uploaded.filename:

        return jsonify({
            "error": "Choose a PDF file first."
        }), 400

    if Path(uploaded.filename).suffix.lower() != ".pdf":

        return jsonify({
            "error": "Only PDF files are supported."
        }), 400

    if ingest_pdf is None:

        return jsonify({
            "error": (
                "PDF ingestion is unavailable. "
                "Define ingest_pdf in tools.my_tools."
            )
        }), 501

    try:

        pdf_bytes = uploaded.read()

        if not pdf_bytes:

            return jsonify({
                "error": "The uploaded PDF is empty."
            }), 400

        # Basic PDF signature check.
        if not pdf_bytes.startswith(b"%PDF-"):

            return jsonify({
                "error": "The uploaded file does not appear to be a valid PDF."
            }), 400

        summary = ingest_pdf(
            pdf_bytes,
            thread_id=thread_id,
            filename=Path(uploaded.filename).name
        )

        if not isinstance(summary, dict):
            summary = {}

        return jsonify({

            "success": True,

            "message": "PDF indexed successfully.",

            "filename": Path(uploaded.filename).name,

            "chunks": summary.get("chunks", 0),

            "documents": summary.get("documents", 0)

        })

    except Exception as exc:

        app.logger.exception("PDF ingestion failed")

        return jsonify({
            "error": f"Could not index PDF: {str(exc)}"
        }), 500


# --------------------------------------------------
# CHAT STREAMING API
# --------------------------------------------------

@app.post("/api/chat")
def chat():

    data = request.get_json(silent=True) or {}

    user_text = (data.get("message") or "").strip()

    thread_id = str(data.get("thread_id") or "").strip()

    if not user_text or not thread_id:

        return jsonify({
            "error": "A message and thread ID are required."
        }), 400

    config = {
        "configurable": {
            "thread_id": thread_id
        },
        "recursion_limit": 50
    }

    @stream_with_context
    def generate():

        try:

            for message_chunk, metadata in chatbot.stream(

                {
                    "messages": [
                        HumanMessage(content=user_text)
                    ]
                },

                config=config,

                stream_mode="messages"

            ):

                # ----------------------------------
                # TOOL EVENTS
                # ----------------------------------

                if isinstance(message_chunk, ToolMessage):

                    tool_name = (
                        getattr(message_chunk, "name", None)
                        or "tool"
                    )

                    yield (
                        "data: "
                        + json.dumps({
                            "type": "tool",
                            "name": tool_name
                        })
                        + "\n\n"
                    )

                    continue

                # ----------------------------------
                # AI EVENTS
                # ----------------------------------

                if not isinstance(message_chunk, AIMessage):
                    continue

                # Do not display AI tool-call messages.
                if getattr(message_chunk, "tool_calls", None):
                    continue

                # Some providers expose tool calls in additional kwargs.
                additional_kwargs = getattr(
                    message_chunk,
                    "additional_kwargs",
                    {}
                ) or {}

                if additional_kwargs.get("tool_calls"):
                    continue

                content = displayable_content(message_chunk.content)

                if not content:
                    continue

                yield (
                    "data: "
                    + json.dumps({
                        "type": "token",
                        "content": content
                    }, ensure_ascii=False)
                    + "\n\n"
                )

            # ----------------------------------
            # COMPLETION EVENT
            # ----------------------------------

            yield (
                "data: "
                + json.dumps({
                    "type": "done"
                })
                + "\n\n"
            )

        except Exception as exc:

            app.logger.exception("Chat streaming failed")

            yield (
                "data: "
                + json.dumps({
                    "type": "error",
                    "error": str(exc)
                }, ensure_ascii=False)
                + "\n\n"
            )

    return Response(

        generate(),

        mimetype="text/event-stream",

        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }

    )


# --------------------------------------------------
# DELETE THREAD API
# --------------------------------------------------

@app.delete("/api/threads/<path:thread_id>")
def api_delete_thread(thread_id):

    thread_id = thread_id.strip()

    if not thread_id:

        return jsonify({
            "success": False,
            "error": "Missing conversation ID."
        }), 400

    try:

        success = delete_thread(thread_id)

        if not success:

            return jsonify({
                "success": False,
                "error": "Database deletion failed."
            }), 500

        return jsonify({
            "success": True,
            "message": "Conversation deleted successfully."
        })

    except Exception as exc:

        app.logger.exception("Thread deletion failed")

        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


# --------------------------------------------------
# FILE SIZE ERROR
# --------------------------------------------------

@app.errorhandler(413)
def file_too_large(error):

    return jsonify({
        "error": "File is too large. Maximum PDF size is 25 MB."
    }), 413


# --------------------------------------------------
# RUN APPLICATION
# --------------------------------------------------

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
        threaded=True
    )
