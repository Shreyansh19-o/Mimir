
// ============================================================
// MIMIR - FRONTEND JAVASCRIPT
// Markdown rendering + Streaming + Chat History + PDF Upload
// ============================================================


// ====================== DOM ELEMENTS ======================

const threadList = document.getElementById("threadList");
const messagesEl = document.getElementById("messages");
const chatArea = document.getElementById("chatArea");
const welcomeEl = document.getElementById("welcome");

const form = document.getElementById("chatForm");
const input = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");

const toastEl = document.getElementById("toast");

const pdfInput = document.getElementById("pdfInput");
const pdfStatus = document.getElementById("pdfStatus");
const uploadProgress = document.getElementById("uploadProgress");

const sidebar = document.getElementById("sidebar");
const menuBtn = document.getElementById("mobileMenuBtn");


// ====================== APPLICATION STATE ======================

let currentThread = null;
let isSending = false;
let toastTimer = null;


// ====================== MARKDOWN CONFIGURATION ======================

// Configure Marked for GitHub-style Markdown.
if (window.marked) {
    marked.setOptions({
        gfm: true,
        breaks: true
    });
}


// Convert Markdown into safe HTML.
function renderMarkdown(content) {
    if (!content) return "";

    // Fallback if Markdown libraries are unavailable.
    if (!window.marked) {
        return escapeHTML(content).replace(/\n/g, "<br>");
    }

    const html = marked.parse(content);

    // Sanitize HTML to prevent injected scripts.
    if (window.DOMPurify) {
        return DOMPurify.sanitize(html, {
            USE_PROFILES: { html: true }
        });
    }

    // Do not insert unsanitized HTML.
    return escapeHTML(content).replace(/\n/g, "<br>");
}


// Escape HTML for safe fallback rendering.
function escapeHTML(text) {
    const element = document.createElement("div");
    element.textContent = text;
    return element.innerHTML;
}


// Render assistant messages as Markdown.
function renderAssistantMessage(bubble, content) {
    bubble.classList.add("markdown-body");
    bubble.innerHTML = renderMarkdown(content);
}


// ====================== MOBILE SIDEBAR ======================

const drawerBackdrop = document.createElement("div");
drawerBackdrop.className = "drawer-backdrop";
drawerBackdrop.setAttribute("aria-hidden", "true");

document.body.appendChild(drawerBackdrop);


function openDrawer() {
    sidebar?.classList.add("open");
    drawerBackdrop.classList.add("show");

    menuBtn?.setAttribute("aria-expanded", "true");
}


function closeDrawer() {
    sidebar?.classList.remove("open");
    drawerBackdrop.classList.remove("show");

    menuBtn?.setAttribute("aria-expanded", "false");
}


function toggleDrawer() {
    sidebar?.classList.contains("open")
        ? closeDrawer()
        : openDrawer();
}


// ====================== PDF UPLOADER POSITION ======================

(function positionPdfUploader() {
    const newChatBtn = document.getElementById("newChatBtn");
    const uploadCard = document.querySelector(".upload-card");

    if (newChatBtn && uploadCard) {
        newChatBtn.insertAdjacentElement("afterend", uploadCard);
    }
})();


// ====================== TOAST NOTIFICATIONS ======================

function toast(message) {
    if (!toastEl) return;

    toastEl.textContent = message;
    toastEl.classList.add("show");

    clearTimeout(toastTimer);

    toastTimer = setTimeout(() => {
        toastEl.classList.remove("show");
    }, 3500);
}


// ====================== CHAT UI HELPERS ======================

function setWelcome() {
    if (!welcomeEl || !messagesEl) return;

    welcomeEl.hidden = messagesEl.children.length > 0;
}


function scrollToBottom() {
    if (!chatArea) return;

    chatArea.scrollTop = chatArea.scrollHeight;
}


function addMessage(role, content = "") {
    if (!messagesEl) return null;

    if (welcomeEl) {
        welcomeEl.hidden = true;
    }

    const row = document.createElement("div");
    row.className = `message ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";

    avatar.textContent = role === "user" ? "◉" : "✧";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    if (role === "assistant") {
        renderAssistantMessage(bubble, content);
    } else {
        bubble.textContent = content;
    }

    row.append(avatar, bubble);

    messagesEl.appendChild(row);

    scrollToBottom();

    return bubble;
}


function clearMessages() {
    if (!messagesEl) return;

    messagesEl.replaceChildren();

    setWelcome();
}


// ====================== CONVERSATION MENU ======================

function closeThreadMenus(except = null) {
    document.querySelectorAll(".thread-menu.show").forEach(menu => {
        if (menu !== except) {
            menu.classList.remove("show");

            const trigger = menu.parentElement?.querySelector(
                ".thread-menu-trigger"
            );

            trigger?.setAttribute("aria-expanded", "false");
        }
    });
}


// ====================== RENDER CONVERSATIONS ======================

function renderThreads(threads) {
    if (!threadList) return;

    threadList.replaceChildren();

    if (!threads.length) {
        const empty = document.createElement("div");

        empty.className = "muted small";
        empty.textContent = "No past conversations yet.";

        threadList.append(empty);
        return;
    }

    threads.slice(0, 30).forEach(thread => {

        const row = document.createElement("div");
        row.className = "thread-row";

        // Conversation title.
        const chatButton = document.createElement("button");

        chatButton.type = "button";

        chatButton.className =
            "thread-item" +
            (thread === currentThread ? " active" : "");

        chatButton.textContent = thread;
        chatButton.title = thread;

        chatButton.addEventListener("click", () => {
            closeThreadMenus();
            closeDrawer();

            openThread(thread);
        });


        // Three-dot options button.
        const menuButton = document.createElement("button");

        menuButton.type = "button";
        menuButton.className = "thread-menu-trigger";

        menuButton.textContent = "⋯";
        menuButton.title = "Conversation options";

        menuButton.setAttribute(
            "aria-label",
            `Options for ${thread}`
        );

        menuButton.setAttribute("aria-expanded", "false");


        // Popup menu.
        const menu = document.createElement("div");
        menu.className = "thread-menu";

        const deleteButton = document.createElement("button");

        deleteButton.type = "button";
        deleteButton.className = "thread-delete-action";

        deleteButton.innerHTML =
            '<span aria-hidden="true">🗑</span> Delete';


        // Toggle popup.
        menuButton.addEventListener("click", event => {
            event.stopPropagation();

            const wasOpen = menu.classList.contains("show");

            closeThreadMenus();

            if (!wasOpen) {
                menu.classList.add("show");

                menuButton.setAttribute("aria-expanded", "true");
            }
        });


        menu.addEventListener("click", event => {
            event.stopPropagation();
        });


        // Delete conversation.
        deleteButton.addEventListener("click", async event => {
            event.stopPropagation();

            closeThreadMenus();

            await deleteChat(thread);
        });


        menu.appendChild(deleteButton);

        row.append(
            chatButton,
            menuButton,
            menu
        );

        threadList.appendChild(row);
    });
}


// ====================== REFRESH CONVERSATIONS ======================

async function refreshThreads() {
    try {
        const response = await fetch("/api/threads");

        if (!response.ok) {
            throw new Error("Could not load conversations.");
        }

        const data = await response.json();

        const threads = Array.isArray(data.threads)
            ? data.threads
            : [];

        // Do not manually add currentThread here.
        // Deleted or nonexistent threads must not reappear.
        renderThreads(threads);

    } catch (error) {
        if (threadList) {
            threadList.textContent =
                "Could not load conversations.";
        }

        console.error("Refresh threads error:", error);
    }
}


// ====================== DELETE CONVERSATION ======================

async function deleteChat(threadId) {

    // Prevent deleting a conversation while it is generating.
    if (isSending && currentThread === threadId) {
        toast(
            "Wait for the response to finish before deleting this chat."
        );

        return;
    }

    const confirmed = window.confirm(
        "Delete this conversation permanently?\n\n" +
        "This action cannot be undone."
    );

    if (!confirmed) return;

    try {
        const response = await fetch(
            `/api/threads/${encodeURIComponent(threadId)}`,
            {
                method: "DELETE"
            }
        );

        const data = await response.json().catch(() => ({}));

        if (!response.ok || !data.success) {
            console.error("Delete API response:", data);

            throw new Error(
                data.error ||
                `Server error: ${response.status}`
            );
        }

        // Reset UI if the active conversation was deleted.
        if (currentThread === threadId) {
            currentThread = null;

            clearMessages();

            if (pdfStatus) {
                pdfStatus.textContent =
                    "Upload a PDF to ask questions about its contents.";
            }

            if (uploadProgress) {
                uploadProgress.textContent = "";
            }
        }

        await refreshThreads();

        toast("Conversation deleted successfully.");

    } catch (error) {
        console.error("Delete error:", error);

        toast(
            error.message || "Unable to delete conversation."
        );
    }
}


// ====================== CREATE NEW CONVERSATION ======================

async function createThread() {
    const response = await fetch("/api/threads", {
        method: "POST"
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok || !data.thread_id) {
        throw new Error(
            data.error || "Could not create a new chat."
        );
    }

    currentThread = data.thread_id;

    clearMessages();

    if (pdfStatus) {
        pdfStatus.textContent =
            "Upload a PDF to ask questions about its contents.";
    }

    if (uploadProgress) {
        uploadProgress.textContent = "";
    }

    await refreshThreads();

    return currentThread;
}


// ====================== PDF STATUS ======================

const DEFAULT_PDF_TEXT = "Upload a PDF to ask questions about its contents.";

async function loadPdfStatus(threadId) {
    if (!pdfStatus) return;

    pdfStatus.textContent = DEFAULT_PDF_TEXT;

    if (uploadProgress) uploadProgress.textContent = "";

    try {
        const response = await fetch(
            `/api/pdf/${encodeURIComponent(threadId)}`
        );

        const data = await response.json();

        // Ignore stale answers if the user already switched chats.
        if (currentThread !== threadId) return;

        if (response.ok && data.indexed) {
            pdfStatus.textContent =
                `${data.filename} · ${data.chunks} chunks from ${data.documents} pages`;
        }
    } catch (error) {
        console.error("PDF status error:", error);
    }
}


// ====================== OPEN EXISTING CONVERSATION ======================

async function openThread(threadId) {
    currentThread = threadId;

    clearMessages();

    try {
        const response = await fetch(
            `/api/history/${encodeURIComponent(threadId)}`
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.error || "Could not load chat."
            );
        }

        (data.messages || []).forEach(message => {
            addMessage(
                message.role,
                message.content || ""
            );
        });

        await refreshThreads();

        loadPdfStatus(threadId);

        scrollToBottom();

    } catch (error) {
        console.error("Open thread error:", error);

        toast(error.message);
    }
}


// ====================== SEND BUTTON STATE ======================

function setSending(value) {
    isSending = value;

    if (sendBtn) {
        sendBtn.disabled = value;
        sendBtn.textContent = value ? "…" : "↑";
    }
}


// ====================== SSE EVENT PARSER ======================

// Process one complete server-sent event.
function processSSEEvent(eventText, state) {
    const lines = eventText.split(/\r?\n/);

    const dataLines = lines
        .filter(line => line.startsWith("data:"))
        .map(line => line.slice(5).trimStart());

    if (!dataLines.length) return;

    const rawData = dataLines.join("\n");

    let payload;

    try {
        payload = JSON.parse(rawData);
    } catch (error) {
        console.error("Invalid SSE JSON:", rawData);
        return;
    }

    if (payload.type === "token") {

        // Keep raw Markdown separately from rendered HTML.
        state.fullResponse += payload.content || "";

        renderAssistantMessage(
            state.answerBubble,
            state.fullResponse
        );

        if (currentThread === state.threadId) {
            scrollToBottom();
        }

        state.status.textContent = "";

    } else if (payload.type === "tool") {

        state.status.textContent =
            `⚙ Using ${payload.name || "tool"}…`;

    } else if (payload.type === "error") {

        throw new Error(
            payload.error || "An error occurred."
        );

    } else if (payload.type === "done") {

        state.status.textContent = "";
        state.completed = true;
    }
}


// ====================== SEND MESSAGE ======================

async function sendMessage(text) {
    if (!text || isSending) return;

    try {
        // Create a new conversation when necessary.
        if (!currentThread) {
            await createThread();
        }

        const sendingThread = currentThread;

        // Display user message.
        addMessage("user", text);

        // Create assistant response bubble.
        const answerBubble = addMessage("assistant", "");

        // Tool activity status.
        const status = document.createElement("div");

        status.className = "tool-status";

        messagesEl.insertBefore(
            status,
            answerBubble.parentElement
        );

        setSending(true);

        const response = await fetch("/api/chat", {
            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                message: text,
                thread_id: sendingThread
            })
        });

        if (!response.ok || !response.body) {
            const data = await response.json().catch(() => ({}));

            throw new Error(
                data.error || "Chat request failed."
            );
        }


        // Streaming state.
        const state = {
            threadId: sendingThread,
            answerBubble,
            status,
            fullResponse: "",
            completed: false
        };


        const reader = response.body.getReader();

        const decoder = new TextDecoder();

        let buffer = "";


        // Read stream.
        while (true) {
            const { value, done } = await reader.read();

            if (done) break;

            buffer += decoder.decode(
                value,
                { stream: true }
            );

            // SSE supports both LF and CRLF separators.
            const events = buffer.split(/\r?\n\r?\n/);

            buffer = events.pop() || "";

            for (const event of events) {
                processSSEEvent(event, state);
            }

            // The server said it is finished: stop reading even if the
            // connection lingers, so the UI can never stay stuck "sending".
            if (state.completed) {
                await reader.cancel().catch(() => {});
                break;
            }
        }


        // Flush decoder's remaining bytes.
        buffer += decoder.decode();


        // Process any final event without a trailing separator.
        if (buffer.trim()) {
            processSSEEvent(buffer, state);
        }


        // Empty response fallback.
        if (!state.fullResponse.trim()) {
            state.fullResponse =
                "Mimir completed the request but returned no text.";

            renderAssistantMessage(
                answerBubble,
                state.fullResponse
            );
        }


        status.textContent = "";

    } catch (error) {
        console.error("Send message error:", error);

        toast(error.message);

        // Show error safely as plain text.
        const lastAssistant = messagesEl?.querySelector(
            ".message.assistant:last-child .bubble"
        );

        if (lastAssistant) {
            lastAssistant.classList.remove("markdown-body");

            lastAssistant.textContent =
                `Sorry, something went wrong: ${error.message}`;
        }

    } finally {
        setSending(false);

        // Refresh the sidebar without blocking the next prompt.
        refreshThreads();

        input?.focus();

        scrollToBottom();
    }
}


// ====================== FORM SUBMISSION ======================

form?.addEventListener("submit", async event => {
    event.preventDefault();

    const text = input.value.trim();

    if (!text || isSending) return;

    input.value = "";
    input.style.height = "auto";

    await sendMessage(text);
});


// ====================== ENTER TO SEND ======================

input?.addEventListener("keydown", event => {
    if (
        event.key === "Enter" &&
        !event.shiftKey &&
        !event.isComposing
    ) {
        event.preventDefault();

        form.requestSubmit();
    }
});


// ====================== AUTO RESIZE INPUT ======================

input?.addEventListener("input", () => {
    input.style.height = "auto";

    input.style.height =
        `${Math.min(input.scrollHeight, 150)}px`;
});


// ====================== NEW CHAT BUTTON ======================

document.getElementById("newChatBtn")?.addEventListener(
    "click",
    () => {
        if (isSending) {
            toast("Wait for the current response to finish.");
            return;
        }

        closeDrawer();

        createThread().catch(error => {
            console.error(error);
            toast(error.message);
        });
    }
);


// ====================== THEME SWITCHER ======================

document.getElementById("themeBtn")?.addEventListener(
    "click",
    event => {
        document.body.classList.toggle("light");

        const light = document.body.classList.contains("light");

        const themeIcon = document.getElementById("themeIcon");

        if (themeIcon) {
            themeIcon.textContent = light ? "☾" : "☼";
        }

        const button = event.currentTarget;

        const textNode = Array.from(button.childNodes).find(
            node => node.nodeType === Node.TEXT_NODE
        );

        if (textNode) {
            textNode.textContent = light
                ? " Switch to dark mode"
                : " Switch to light mode";
        }
    }
);


// ====================== MOBILE SIDEBAR EVENTS ======================

menuBtn?.addEventListener("click", toggleDrawer);

drawerBackdrop.addEventListener("click", closeDrawer);


// ====================== GLOBAL CLICK EVENTS ======================

document.addEventListener("click", event => {
    if (!event.target.closest(".thread-row")) {
        closeThreadMenus();
    }
});


document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
        closeDrawer();
        closeThreadMenus();
    }
});


// ====================== SUGGESTED PROMPTS ======================

document.querySelectorAll(".suggestion").forEach(button => {
    button.addEventListener("click", () => {
        input.value = button.dataset.prompt || "";

        input.focus();

        input.dispatchEvent(new Event("input"));
    });
});


// ====================== PDF UPLOAD ======================

pdfInput?.addEventListener("change", async () => {
    const file = pdfInput.files?.[0];

    if (!file) return;

    try {
        if (isSending) {
            throw new Error(
                "Wait for the current response to finish before uploading."
            );
        }

        if (!currentThread) {
            await createThread();
        }

        const formData = new FormData();

        formData.append("file", file);
        formData.append("thread_id", currentThread);

        if (uploadProgress) {
            uploadProgress.textContent = "Indexing PDF…";
        }

        const response = await fetch("/api/upload", {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.error || "PDF upload failed."
            );
        }

        if (pdfStatus) {
            pdfStatus.textContent =
                `${data.filename} · ${data.chunks} chunks from ${data.documents} pages`;
        }

        if (uploadProgress) {
            uploadProgress.textContent =
                "✓ PDF indexed. You can now ask questions about it.";
        }

        toast("PDF indexed successfully.");

    } catch (error) {
        console.error("PDF upload error:", error);

        if (uploadProgress) {
            uploadProgress.textContent = error.message;
        }

        toast(error.message);

    } finally {
        pdfInput.value = "";
    }
});


// ====================== INITIALIZE MIMIR ======================

(async function init() {
    try {
        await refreshThreads();

        const response = await fetch("/api/threads");

        if (!response.ok) {
            throw new Error("Could not initialize conversations.");
        }

        const data = await response.json();

        if (data.threads && data.threads.length > 0) {

            // Open the most recent conversation.
            await openThread(data.threads[0]);

        } else {

            // Start a fresh conversation.
            await createThread();
        }

    } catch (error) {
        console.error("Mimir initialization error:", error);

        toast(error.message);
    }
})();
