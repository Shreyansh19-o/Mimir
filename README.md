# Mimir — AI Assistant

Mimir is a chat assistant built with **LangGraph**, **LangChain** and **Flask**. It streams answers to a clean web UI and can use tools: web search, PDF question-answering (RAG), YouTube transcript summarization, stock prices and a calculator. Conversations are saved in SQLite, so they survive restarts.

## Features

- **Streaming chat** over Server-Sent Events, with live "Using tool…" status.
- **Persistent conversations** (SQLite checkpointer): list, reopen and delete past chats.
- **PDF knowledge base** — upload a PDF per chat; it is chunked, embedded (`all-MiniLM-L6-v2`) and stored in a FAISS index on disk. The `rag_tool` retrieves passages with page references.
- **YouTube summarization** — fetches captions (yt-dlp, then youtube-transcript-api; English/Hindi) and condenses long transcripts.
- **Web search** — DuckDuckGo (no key) and optional Tavily.
- **Stock prices** (Alpha Vantage) and a basic **calculator**.
- Markdown rendering with DOMPurify sanitizing, dark/light theme, mobile-friendly drawer sidebar.

## Project structure

```
MIMIR/
├── app.py            # Flask app and REST/SSE API
├── lg_db_be.py       # LangGraph graph, system prompt, SQLite checkpointing
├── llm_config.py     # LLM setup (Groq, openai/gpt-oss-120b)
├── tools/
│   └── my_tools.py   # Tools: search, stocks, calculator, YouTube, PDF RAG
├── templates/index.html
├── static/css/style.css
├── static/js/script.js
├── requirements.txt
└── .env.example
```

Created at runtime: `chatbot.db` (conversations) and `faiss_indexes/` (PDF indexes).

## Requirements

- Python 3.10+ (developed on 3.13)
- A [Groq](https://console.groq.com) API key
- Internet access (LLM, search, model download on first run)

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env            # then edit .env
```

### Environment variables (`.env`)

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEY` | **Yes** | LLM access |
| `TAVILY_API_KEY` | No | Enables Tavily search |
| `ALPHAVANTAGE_API_KEY` | No | Enables the stock price tool |
| `MIMIR_DEFAULT_PDF` | No | PDF indexed at startup as a shared fallback knowledge base (default `documents/company.pdf` if present) |
| `MIMIR_FAISS_DIR` | No | Where PDF indexes are stored (default `faiss_indexes/`) |
| `FLASK_DEBUG` | No | `1` (default) or `0` to turn debug mode off |

## Run

```bash
python app.py
```

Open <http://127.0.0.1:5000>. The first start downloads the embedding model, so it can take a minute.

## Using Mimir

1. Click **New chat** (open the sidebar with the ☰ button) or just start typing.
2. To chat with a document, click **Add PDF** under *Knowledge base*, wait for "PDF indexed", then ask questions. Each chat has its own PDF.
3. Paste a YouTube link and ask for a summary.
4. Use the **⋯** menu next to a conversation to delete it (this also deletes its PDF index).

## API reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Web UI |
| GET | `/api/threads` | List conversations, most recently active first |
| POST | `/api/threads` | Create a new thread ID |
| GET | `/api/history/<thread_id>` | Messages of a conversation |
| POST | `/api/chat` | Send `{message, thread_id}`; streams SSE events `tool`, `token`, `done`, `error` |
| POST | `/api/upload` | Multipart `file` (PDF, max 25 MB) + `thread_id` |
| GET | `/api/pdf/<thread_id>` | Indexed-PDF info for a chat |
| DELETE | `/api/threads/<thread_id>` | Delete a conversation and its PDF index |

## Customizing

- **Switch the model:** edit `get_llm()` in `llm_config.py` (e.g. another Groq model, or Gemini after installing `langchain-google-genai`).
- **Add a tool:** write a function decorated with `@tool` in `tools/my_tools.py` and append it to `all_tools`. Its description is injected into Mimir's system prompt automatically.
- **Change behavior/tone:** edit `MIMIR_SYSTEM_PROMPT` in `lg_db_be.py`.

## Troubleshooting

- **`GROQ_API_KEY not found`** — create `.env` from `.env.example` and add the key.
- **Chat stops responding after an error** — check the terminal running `app.py`; Groq rate limits (HTTP 429) are the most common cause. Wait a moment and retry.
- **"No PDF index is available"** — wait for the "PDF indexed" message after uploading; scanned (image-only) PDFs contain no extractable text and cannot be indexed.
- **YouTube transcript unavailable** — the video may have no captions or restrict access.

## Security notes

- Intended for local use: it binds to `127.0.0.1` and has no authentication. Add authentication and turn off debug mode (`FLASK_DEBUG=0`) before exposing it.
- FAISS indexes are loaded with `allow_dangerous_deserialization=True`; this is safe only because the app creates them itself. Never copy untrusted index folders into `faiss_indexes/`.
- Do not commit `.env` or `chatbot.db` (both are in `.gitignore`).

## Author

Created by Shreyansh Nayal.
