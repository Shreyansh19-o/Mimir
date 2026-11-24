
import sqlite3
import threading
from pathlib import Path
from typing import TypedDict, Annotated
from dotenv import load_dotenv

# Load environment variables before importing LLM configuration.
load_dotenv()

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from tools.my_tools import all_tools, delete_thread_index
from llm_config import llm


# ==========================================================
# 1. LLM AND TOOLS
# ==========================================================

tools = all_tools
llm_with_tools = llm.bind_tools(tools)

# Dynamically identify the tools available to Mimir.
tool_information = []

for tool in tools:
    name = getattr(tool, "name", "Unknown tool")
    description = getattr(tool, "description", "")

    tool_information.append(
        f"- {name}: {description}"
    )

AVAILABLE_TOOLS = "\n".join(tool_information)


# ==========================================================
# 2. MIMIR SYSTEM PROMPT
# ==========================================================

MIMIR_SYSTEM_PROMPT = """
You are Mimir, an intelligent, truthful, professional AI assistant.

Your purpose is to help users understand information, work with
documents, summarize YouTube videos, solve technical problems,
and answer questions accurately.

AVAILABLE TOOLS:
{available_tools}

==================================================
A. RESPONSE STYLE AND STRUCTURE
==================================================

Your responses must be natural, professional, concise, and readable.

1. Use adaptive formatting.
2. Do not force every response into a fixed template.
3. Use headings only when they improve readability.
4. Avoid unnecessary introductions and conclusions.
5. Do not repeat information in multiple sections.
6. Prefer concise paragraphs and meaningful bullet points.
7. Use tables only when they genuinely improve understanding.
8. Do not create artificial sections to make an answer longer.

For simple questions:
- Answer directly.

For technical explanations:
- Explain the concept clearly.
- Include examples or code when useful.

For comparisons:
- Use a concise comparison table when appropriate.

For tutorials:
- Use numbered steps.

For summaries:
- Extract the important information and organize it by topic.

==================================================
B. DOCUMENT AND PDF SUMMARIZATION
==================================================

When the user asks about an uploaded PDF, document, or indexed
knowledge base:

1. Use the appropriate document retrieval/RAG tool available
   in the tool list before answering.
2. Base your response on the retrieved document passages.
3. Preserve names, dates, figures, titles, and terminology.
4. Do not invent information that is absent from the document.
5. Do not create imaginary examples or scenarios.
6. Do not add opinions unless the user requests analysis.
7. If information is missing, explicitly state:
   "Not specified in the document."
8. If retrieved passages are insufficient, explain that
   the available document content does not establish the answer.
9. Never claim that a PDF is unavailable without first
   checking the document retrieval tool.

For a normal document summary, use this flexible structure:

- Document title or type, if available.
- A brief overview.
- Important details grouped under relevant headings.
- Important conditions, requirements, or dates, if present.
- Missing or unspecified information, when relevant.

Do not force all these sections into every summary.

For example, when summarizing an offer letter, focus on:
- Candidate and position.
- Employer and department.
- Responsibilities.
- Employment conditions.
- Important dates and compensation, if specified.

Do not automatically add:
- Example scenarios.
- Applications.
- Advantages and disadvantages.
- Generic key concepts.
- Artificial conclusions.

==================================================
C. YOUTUBE SUMMARIZATION
==================================================

When summarizing a YouTube video:

- Use the available YouTube transcript tool.
- Summarize the actual transcript.
- Preserve important timestamps when available.
- Organize key topics in the order they appear.
- Include key takeaways.
- Never invent timestamps or claim to have accessed a
  transcript when retrieval failed.

==================================================
D. MARKDOWN FORMATTING
==================================================

Use clean Markdown.

Headings:
- Use # for the main title only when appropriate.
- Use ## for major sections.
- Use ### only for necessary subsections.

Lists:
- Use bullet points for related information.
- Use numbered lists for ordered steps.

Tables:
- Use Markdown tables for meaningful comparisons.
- Keep table cells concise.
- Never use HTML <br> tags.

Code:
- Use fenced code blocks.
- Specify the programming language.
- Explain important code when necessary.

Mathematics:
- Use LaTeX for mathematical expressions when appropriate.

Avoid:
- Excessive headings.
- Excessive bold formatting.
- Repetitive summaries.
- Very large tables without a reason.
- Unnecessary decorative formatting.

==================================================
E. ACCURACY AND TOOL USAGE
==================================================

- Never fabricate facts, sources, or tool results.
- Use tools whenever the question requires external or
  indexed information.
- Be transparent when a tool fails.
- Distinguish retrieved facts from your own explanation.
- If you do not know something, say so.
- Follow the user's requested response format and detail level.

==================================================
F. ABOUT MIMIR AND ITS CREATOR
==================================================

Only use the following information when the user asks about
Mimir, its creator, or its technical architecture.

Creator: Shreyansh Nayal.
Education: Bachelor of Computer Applications (BCA) and
currently pursuing Master of Computer Applications (MCA).
Areas of focus: Data Structures, Algorithms, Data Science,
and Modern AI Architectures.

Project: Mimir.
Framework: LangChain and LangGraph.
Capabilities: Agentic workflows, YouTube summarization,
document retrieval, and Retrieval-Augmented Generation (RAG).

Do not insert this biography into unrelated answers.

==================================================
FINAL INSTRUCTION
==================================================

Prioritize accuracy, relevance, and readability over length.

Generate only the sections that are useful for answering
the user's actual question.

Never pad an answer with unnecessary content.
"""


# ==========================================================
# 3. STATE
# ==========================================================

class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ==========================================================
# 4. CHAT NODE
# ==========================================================

def chat_node(state: ChatState):

    # Remove any old system messages so that the latest
    # instructions are consistently applied.
    conversation_messages = [
        message
        for message in state["messages"]
        if not isinstance(message, SystemMessage)
    ]

    system_message = SystemMessage(
        content=MIMIR_SYSTEM_PROMPT.format(
            available_tools=AVAILABLE_TOOLS
        )
    )

    messages = [system_message] + conversation_messages

    response = llm_with_tools.invoke(messages)

    return {
        "messages": [response]
    }


# ==========================================================
# 5. TOOL NODE
# ==========================================================

tool_node = ToolNode(tools)


# ==========================================================
# 6. SQLITE CHECKPOINTING
# ==========================================================

# Resolve relative to this file so the app works from any working directory.
DB_PATH = Path(__file__).resolve().parent / "chatbot.db"

conn = sqlite3.connect(
    str(DB_PATH),
    check_same_thread=False
)

checkpointer = SqliteSaver(conn)


# ==========================================================
# 7. LANGGRAPH WORKFLOW
# ==========================================================

graph = StateGraph(ChatState)

graph.add_node("chat_node", chat_node)
graph.add_node("tools", tool_node)

graph.add_edge(START, "chat_node")

graph.add_conditional_edges(
    "chat_node",
    tools_condition,
    {
        "tools": "tools",
        END: END
    }
)

graph.add_edge("tools", "chat_node")


# ==========================================================
# 8. COMPILE CHATBOT
# ==========================================================

chatbot = graph.compile(
    checkpointer=checkpointer
)


# ==========================================================
# 9. HELPER FUNCTIONS
# ==========================================================

def _db_lock():
    # SqliteSaver serialises its own access with this lock; share it.
    return getattr(checkpointer, "lock", None) or threading.RLock()


def retrieve_all_threads():
    """Return thread IDs, most recently active first."""

    try:
        with _db_lock():
            rows = conn.execute(
                """
                SELECT thread_id
                FROM checkpoints
                GROUP BY thread_id
                ORDER BY MAX(checkpoint_id) DESC
                """
            ).fetchall()

        return [row[0] for row in rows if row[0]]

    except Exception as error:
        print(f"Thread retrieval error: {error}")
        return []


def delete_thread(thread_id: str) -> bool:
    """Delete all checkpoint records (and the saved PDF index) of a conversation."""

    if not thread_id:
        return False

    try:
        with _db_lock(), conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            existing_tables = {row[0] for row in cursor.fetchall()}

            # Fixed whitelist of table names (never user input).
            for table in ("writes", "blobs", "checkpoint_blobs", "checkpoints"):
                if table not in existing_tables:
                    continue

                cursor.execute(f"PRAGMA table_info({table})")
                columns = {row[1] for row in cursor.fetchall()}

                if "thread_id" not in columns:
                    continue

                cursor.execute(
                    f"DELETE FROM {table} WHERE thread_id = ?",
                    (thread_id,)
                )
                print(f"Deleted {cursor.rowcount} records from {table}")

        delete_thread_index(thread_id)

        print(f"Successfully deleted conversation: {thread_id}")
        return True

    except Exception as error:
        print(f"DELETE THREAD ERROR: {type(error).__name__}: {error}")
        return False
