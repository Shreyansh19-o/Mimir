import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from llm_config import llm

load_dotenv()

# -------------------

embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

# ====================== PERSISTENT PDF MEMORY ======================
# Indexes are saved on disk and can be loaded after a chatbot restart.
BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_ROOT = Path(os.getenv("MIMIR_FAISS_DIR", str(BASE_DIR / "faiss_indexes")))
INDEX_ROOT.mkdir(parents=True, exist_ok=True)

_THREAD_RETRIEVERS: Dict[str, Any] = {}
_THREAD_METADATA: Dict[str, dict] = {}


def _index_dir(thread_id: str) -> Path:
    """Return a stable, filesystem-safe directory for a thread's index."""
    key = hashlib.sha256(str(thread_id).encode("utf-8")).hexdigest()
    return INDEX_ROOT / key


def _read_metadata(thread_id: str) -> dict:
    metadata_path = _index_dir(thread_id) / "metadata.json"
    if not metadata_path.is_file():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_retriever(thread_id: Optional[str]):
    """Load a thread's saved PDF index, falling back to the default index."""
    requested_id = str(thread_id) if thread_id is not None else None
    candidates = []
    if requested_id:
        candidates.append(requested_id)
    if requested_id != "default":
        candidates.append("default")

    for candidate in candidates:
        if candidate in _THREAD_RETRIEVERS:
            return _THREAD_RETRIEVERS[candidate]

        index_path = _index_dir(candidate)
        if not (index_path / "index.faiss").is_file():
            continue

        try:
            # Only load indexes created and stored locally by this application.
            vector_store = FAISS.load_local(
                str(index_path),
                embeddings,
                allow_dangerous_deserialization=True,
            )
            retriever = vector_store.as_retriever(
                search_type="similarity",
                search_kwargs={"k": 4},
            )
            _THREAD_RETRIEVERS[candidate] = retriever
            _THREAD_METADATA[candidate] = _read_metadata(candidate)
            return retriever
        except Exception as exc:
            print(f"Could not load PDF index '{candidate}': {exc}")

    return None


def ingest_pdf(
    file_bytes: bytes,
    thread_id: str,
    filename: Optional[str] = None,
    source_path: Optional[str] = None,
) -> dict:
    """Index PDF bytes and persist the FAISS index for future restarts."""
    if not file_bytes:
        raise ValueError("No bytes received for ingestion.")

    thread_id = str(thread_id)
    index_path = _index_dir(thread_id)
    index_path.mkdir(parents=True, exist_ok=True)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(file_bytes)
            temp_path = temp_file.name

        docs = PyPDFLoader(temp_path).load()
        if not docs:
            raise ValueError("The PDF contains no readable pages.")

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_documents(docs)
        if not chunks:
            raise ValueError("No text could be extracted from the PDF.")

        vector_store = FAISS.from_documents(chunks, embeddings)
        vector_store.save_local(str(index_path))

        metadata = {
            "filename": filename or "uploaded.pdf",
            "documents": len(docs),
            "chunks": len(chunks),
            "source_path": str(Path(source_path).resolve()) if source_path else None,
        }
        if source_path:
            source = Path(source_path).resolve()
            stat = source.stat()
            metadata["source_mtime_ns"] = stat.st_mtime_ns
            metadata["source_size"] = stat.st_size

        (index_path / "metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        _THREAD_RETRIEVERS[thread_id] = vector_store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 4},
        )
        _THREAD_METADATA[thread_id] = metadata
        return metadata
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def get_pdf_info(thread_id: str) -> dict:
    """Return saved PDF metadata for exactly this thread (no default fallback)."""
    if not (_index_dir(str(thread_id)) / "index.faiss").is_file():
        return {}
    return _read_metadata(str(thread_id))


def delete_thread_index(thread_id: str) -> None:
    """Remove a thread's saved PDF index (called when a conversation is deleted)."""
    import shutil

    thread_id = str(thread_id)
    if thread_id == "default":
        return
    _THREAD_RETRIEVERS.pop(thread_id, None)
    _THREAD_METADATA.pop(thread_id, None)
    shutil.rmtree(_index_dir(thread_id), ignore_errors=True)


def load_default_pdf(pdf_path: str) -> dict:
    """Load a configured PDF on startup, indexing only if it has changed."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Default PDF not found: {path}")

    stat = path.stat()
    metadata = _read_metadata("default")
    index_exists = (_index_dir("default") / "index.faiss").is_file()
    unchanged = (
        index_exists
        and metadata.get("source_path") == str(path)
        and metadata.get("source_mtime_ns") == stat.st_mtime_ns
        and metadata.get("source_size") == stat.st_size
    )

    if unchanged and _get_retriever("default") is not None:
        print(f"Loaded saved PDF memory: {path.name}")
        return metadata

    result = ingest_pdf(
        file_bytes=path.read_bytes(),
        thread_id="default",
        filename=path.name,
        source_path=str(path),
    )
    print(
        f"Indexed default PDF: {path.name} "
        f"({result['documents']} pages, {result['chunks']} chunks)"
    )
    return result


# Optional automatic startup ingestion. Set MIMIR_DEFAULT_PDF in .env,
# or place your file at documents/company.pdf.
_default_pdf_path = os.getenv("MIMIR_DEFAULT_PDF", str(BASE_DIR / "documents" / "company.pdf"))
if Path(_default_pdf_path).expanduser().is_file():
    try:
        load_default_pdf(_default_pdf_path)
    except Exception as exc:
        print(f"Default PDF loading failed: {exc}")
else:
    print(
        "Default PDF not loaded: set MIMIR_DEFAULT_PDF in .env "
        "or add documents/company.pdf."
    )


# ====================== Tool Initializations ======================

# Standard web search (no API key required).
search_tool = DuckDuckGoSearchRun(region="us-en")

# Tavily search (optional, needs TAVILY_API_KEY; the key is read from the environment).
tavily_search = None
if os.getenv("TAVILY_API_KEY"):
    try:
        try:
            from langchain_tavily import TavilySearch

            tavily_search = TavilySearch(
                max_results=3,
                include_answer=True,
                search_depth="advanced",
            )
        except ImportError:
            from langchain_community.tools.tavily_search import TavilySearchResults

            # raw content is intentionally disabled: it can overflow the model's context.
            tavily_search = TavilySearchResults(
                max_results=3,
                include_answer=True,
                include_raw_content=False,
                search_depth="advanced",
            )
    except Exception as exc:
        print(f"Tavily search disabled: {exc}")
else:
    print("Warning: TAVILY_API_KEY not found in .env. Tavily search will be disabled.")

# ====================== Functional Tools ======================

@tool

def calculator(first_num: float, second_num: float, operation: str) -> str:

    """Perform basic arithmetic operations: add, sub, mul, or div."""

    try:

        if operation == "add":

            result = first_num + second_num

        elif operation == "sub":

            result = first_num - second_num

        elif operation == "mul":

            result = first_num * second_num

        elif operation == "div":

            if second_num == 0:

                return "Error: Division by zero is not allowed."

            result = first_num / second_num

        else:

            return f"Error: Unsupported operation '{operation}'."

        return f"Result: {result}"

    except Exception as e:

        return f"Error: {str(e)}"

@tool
def get_stock_price(symbol: str) -> str:
    """Fetch the latest stock price for a given symbol like AAPL or TSLA."""
    symbol = (symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", symbol):
        return "Error: Invalid stock symbol."

    api_key = os.getenv("ALPHAVANTAGE_API_KEY", "")
    if not api_key:
        return "Error: Set ALPHAVANTAGE_API_KEY in your .env file."

    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key},
            timeout=10,
        )
        data = r.json()
    except Exception as e:
        return f"Error fetching stock price: {e}"

    price = (data.get("Global Quote") or {}).get("05. price")
    if price:
        return f"Current price of {symbol}: ${price}"

    # Alpha Vantage reports rate limits / bad symbols in these keys.
    note = data.get("Note") or data.get("Information") or data.get("Error Message")
    return f"Could not fetch a price for {symbol}." + (f" Provider message: {note}" if note else "")


# ====================== YouTube helpers ======================

_CAPTION_LANGS = ["en", "en-US", "en-GB", "hi"]
_VTT_TIME_RE = re.compile(
    r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}.*"
)


def extract_video_id(url: str) -> str:
    """Extract the video ID from watch, youtu.be, shorts, embed and live URLs."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()

    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0]

    if host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [""])[0]
        match = re.match(r"^/(?:shorts|embed|live|v)/([\w-]{11})", parsed.path)
        if match:
            return match.group(1)

    return ""


def clean_caption_text(raw: str) -> str:
    """Turn VTT/SRT/JSON3 caption data into plain, de-duplicated text."""
    raw = (raw or "").strip()
    if raw.startswith("{"):
        try:
            events = json.loads(raw).get("events", [])
            pieces = ["".join(seg.get("utf8", "") for seg in ev.get("segs", [])) for ev in events]
            return re.sub(r"\s+", " ", " ".join(pieces)).strip()
        except (ValueError, AttributeError):
            pass

    import html

    lines, previous = [], None
    for line in re.sub(r"<[^>]+>", "", raw).splitlines():
        line = line.strip()
        if (
            not line
            or line.isdigit()
            or line.startswith(("WEBVTT", "Kind:", "Language:"))
            or _VTT_TIME_RE.match(line)
        ):
            continue
        line = html.unescape(line)
        if line == previous:  # auto-captions repeat each line
            continue
        lines.append(line)
        previous = line
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _transcript_via_yt_dlp(url: str):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": _CAPTION_LANGS,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    tracks = None
    for source in (subtitles, automatic):
        for lang in _CAPTION_LANGS:
            if source.get(lang):
                tracks = source[lang]
                break
        if tracks:
            break
    if not tracks:
        return None

    # Prefer VTT, which clean_caption_text handles best.
    track = next((t for t in tracks if t.get("ext") == "vtt"), tracks[0])
    response = requests.get(track["url"], timeout=20)
    if response.status_code != 200:
        return None

    text = clean_caption_text(response.text)
    if not text:
        return None
    return (
        info.get("title", "Unknown Title"),
        info.get("uploader", "Unknown Channel"),
        info.get("duration"),
        text,
        "yt-dlp",
    )


def _transcript_via_api(url: str):
    """youtube-transcript-api, compatible with both the 0.x and 1.x interfaces."""
    from youtube_transcript_api import YouTubeTranscriptApi

    video_id = extract_video_id(url)
    if not video_id:
        return None

    if hasattr(YouTubeTranscriptApi, "list_transcripts"):  # 0.x
        listing = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = listing.find_transcript(_CAPTION_LANGS)
        except Exception:
            transcript = listing.find_generated_transcript(_CAPTION_LANGS)
        entries = transcript.fetch()
    else:  # 1.x
        entries = YouTubeTranscriptApi().fetch(video_id, languages=_CAPTION_LANGS)

    formatted = []
    for entry in entries:
        if isinstance(entry, dict):
            start, text = entry.get("start", 0), entry.get("text", "")
        else:
            start, text = entry.start, entry.text
        formatted.append(f"[{int(start // 60):02d}:{int(start % 60):02d}] {text}")

    if not formatted:
        return None
    return "Unknown Title", "Unknown Channel", None, "\n".join(formatted), "youtube-transcript-api"


def _summarize_transcript(transcript: str, method: str) -> str:
    """Condense a long transcript with the configured LLM (chunked, capped)."""
    words = transcript.split()
    chunk_words, max_chunks = 2500, 3
    chunks = [
        " ".join(words[i:i + chunk_words])
        for i in range(0, len(words), chunk_words)
    ][:max_chunks]

    parts = []
    for number, chunk in enumerate(chunks, start=1):
        prompt = PromptTemplate.from_template(
            "Summarize this part of a YouTube transcript in 150-250 words. "
            "Keep timestamps and names if present.\n\n{transcript}"
        )
        parts.append(f"Part {number}: " + (prompt | llm).invoke({"transcript": chunk}).content)

    note = ""
    if len(words) > chunk_words * max_chunks:
        note = f" Only the first {chunk_words * max_chunks} of {len(words)} words were summarized."
    return f"Summary (transcript too long for full text, fetched via {method}).{note}\n" + "\n\n".join(parts)


@tool
def get_youtube_transcript(youtube_url: str) -> str:
    """
    Fetch video title, channel, duration and transcript from a YouTube URL.
    Tries yt-dlp first, then youtube-transcript-api (English/Hindi, with timestamps).
    Very long transcripts are condensed to avoid token limits.
    """
    if not extract_video_id(youtube_url):
        return f"Error: '{youtube_url}' is not a recognizable YouTube video URL."

    result = None
    for fetcher in (_transcript_via_yt_dlp, _transcript_via_api):
        try:
            result = fetcher(youtube_url)
        except Exception as exc:
            print(f"{fetcher.__name__} failed: {type(exc).__name__}: {exc}")
            result = None
        if result:
            break

    if not result:
        return (
            "YouTube Video:\n"
            f"URL: {youtube_url}\n"
            "Status: Could not retrieve transcript from any method.\n"
            "Reason: No captions available or video restrictions.\n"
            "Note to AI: You do not have the actual transcript. Be honest with the user. "
            "Suggest they provide key points manually or try a public video with captions enabled."
        )

    title, author, duration, transcript, method = result

    if len(transcript.split()) > 2000:
        try:
            output = _summarize_transcript(transcript, method)
        except Exception as exc:
            print(f"Transcript summarization failed: {exc}")
            output = f"Transcript (truncated due to summarization error, fetched via {method}):\n{transcript[:5000]}..."
    else:
        output = f"Full Transcript (fetched via {method}):\n{transcript}"

    duration_line = f"Duration: {duration} seconds\n" if duration else ""
    return (
        "YouTube Video Details:\n"
        f"Title: {title}\n"
        f"Channel: {author}\n"
        f"{duration_line}"
        f"URL: {youtube_url}\n\n"
        f"{output}\n\n"
        "Use the above transcript to answer questions accurately about the video content."
    )


@tool
def rag_tool(query: str, config: RunnableConfig) -> str:
    """Search the current chat's PDF, falling back to the persistent default PDF."""
    configurable = (config or {}).get("configurable", {})
    thread_id = configurable.get("thread_id")
    requested_id = str(thread_id) if thread_id is not None else None

    retriever = _get_retriever(requested_id)
    if retriever is None:
        return (
            "No PDF index is available. Check MIMIR_DEFAULT_PDF or upload a PDF "
            "and ensure indexing finishes before asking document questions."
        )

    results = retriever.invoke(query)
    if not results:
        return "No relevant passages were found in the indexed PDF for this question."

    # Resolve the metadata for the retriever that was selected.
    metadata_id = requested_id if requested_id in _THREAD_RETRIEVERS else "default"
    metadata = _THREAD_METADATA.get(metadata_id) or _read_metadata(metadata_id)
    source_file = metadata.get("filename", "uploaded PDF")

    passages = []
    for index, doc in enumerate(results, start=1):
        page = doc.metadata.get("page")
        page_label = f", page {page + 1}" if isinstance(page, int) else ""
        passages.append(
            f"[Source: {source_file}{page_label}; passage {index}]\n"
            f"{doc.page_content}"
        )

    return "\n\n".join(passages)


# async def main():

#     mcp_client = MultiServerMCPClient(

#         {

#             "my_fastmcp_server": {

#                 "transport": "http",

#                 "url": "https://graceful-maroon-perch.fastmcp.app/mcp",

#             }

#         }

#     )

#     tools = await mcp_client.get_tools()

#     model = ChatOpenAI(model="gpt-4o")

#     agent = create_react_agent(model, tools=tools)

#     inputs = {"messages": [("user", "Use the tool from the graceful-maroon-perch server to help me.")]}

#     async for chunk in agent.astream(inputs, stream_mode="values"):

#         message = chunk["messages"][-1]

#         message.pretty_print()

# if __name__ == "__main__":

#     asyncio.run(main())

# ====================== Bind Tools ======================

all_tools = [search_tool, get_stock_price, calculator, get_youtube_transcript, rag_tool]

if tavily_search:

    all_tools.append(tavily_search)
