from __future__ import annotations

import base64
import queue
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from openai import OpenAI
except ImportError:  # Allows the app to show its setup message before dependencies are installed.
    OpenAI = None


APP_DIR = Path(__file__).parent
DATABASE_PATH = APP_DIR / "image_chat.db"
IMAGE_DIR = APP_DIR / "generated_images"
IMAGE_DIR.mkdir(exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    connection = connect_db()
    try:
        with connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                    prompt TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
    finally:
        connection.close()


def create_conversation(title: str = "New image session") -> str:
    conversation_id = str(uuid.uuid4())
    timestamp = now_iso()
    connection = connect_db()
    try:
        with connection:
            connection.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title, timestamp, timestamp),
            )
    finally:
        connection.close()
    return conversation_id


def list_conversations() -> list[sqlite3.Row]:
    connection = connect_db()
    try:
        return connection.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        connection.close()


def get_messages(conversation_id: str) -> list[sqlite3.Row]:
    connection = connect_db()
    try:
        return connection.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, id",
            (conversation_id,),
        ).fetchall()
    finally:
        connection.close()


def add_message(conversation_id: str, role: str, content: str) -> int:
    timestamp = now_iso()
    connection = connect_db()
    try:
        with connection:
            cursor = connection.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, timestamp),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (timestamp, conversation_id),
            )
            return int(cursor.lastrowid)
    finally:
        connection.close()


def update_conversation_title(conversation_id: str, title: str) -> None:
    connection = connect_db()
    try:
        with connection:
            connection.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title[:60], now_iso(), conversation_id),
            )
    finally:
        connection.close()


def count_conversation_images(conversation_id: str) -> int:
    connection = connect_db()
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM images WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    finally:
        connection.close()
    return int(row["total"])


def add_image_record(
    conversation_id: str,
    message_id: int,
    prompt: str,
    file_path: str,
    model: str,
) -> None:
    connection = connect_db()
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO images (conversation_id, message_id, prompt, file_path, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, message_id, prompt, file_path, model, now_iso()),
            )
    finally:
        connection.close()


def load_conversation(conversation_id: str) -> None:
    st.session_state.active_conversation_id = conversation_id
    st.session_state.messages = [dict(message) for message in get_messages(conversation_id)]


def ensure_active_conversation() -> None:
    if "active_conversation_id" not in st.session_state:
        conversations = list_conversations()
        conversation_id = conversations[0]["id"] if conversations else create_conversation()
        load_conversation(conversation_id)


def render_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        image_path = message.get("image_path")
        if image_path and Path(image_path).exists():
            st.image(image_path, width="stretch")


def generate_image(api_key: str, prompt: str, model: str, size: str) -> tuple[bytes, str]:
    if OpenAI is None:
        raise RuntimeError("The OpenAI package is not installed. Run: pip install -r requirements.txt")
    client = OpenAI(api_key=api_key)
    result = client.images.generate(model=model, prompt=prompt, size=size)
    image_data: Any = result.data[0]
    encoded = getattr(image_data, "b64_json", None)
    if not encoded:
        raise RuntimeError("The image API returned no image data.")
    return base64.b64decode(encoded), "png"


# ---------------------------------------------------------------------------
# Background generation queue.
#
# Images are generated on a dedicated daemon thread that lives completely
# outside Streamlit's rerun cycle. Nothing the user does in the UI — sending
# new prompts, adding a schedule, switching conversations, clicking anything —
# can interrupt or restart an image that is already being generated. The
# thread pulls jobs from a FIFO queue, so prompts are always generated
# strictly one after another, in the order they were scheduled.
# ---------------------------------------------------------------------------


@st.cache_resource
def worker() -> dict[str, Any]:
    """Process-wide queue, worker thread and live state; survives reruns."""

    resources: dict[str, Any] = {
        "jobs": queue.Queue(),
        "results": queue.Queue(),
        "state": {"current": None},
    }

    def run_worker() -> None:
        while True:
            job: dict[str, Any] = resources["jobs"].get()
            prompt = job["prompt"]
            conversation_id = job["conversation_id"]
            resources["state"]["current"] = prompt
            user_message_id = add_message(conversation_id, "user", prompt)
            try:
                image_bytes, extension = generate_image(job["api_key"], prompt, job["model"], job["size"])
                image_path = IMAGE_DIR / f"{conversation_id}-{uuid.uuid4().hex}.{extension}"
                image_path.write_bytes(image_bytes)
                response_text = "Created your image. It is saved with this conversation."
                assistant_message_id = add_message(conversation_id, "assistant", response_text)
                add_image_record(conversation_id, assistant_message_id, prompt, str(image_path), job["model"])
                if count_conversation_images(conversation_id) == 1:
                    update_conversation_title(conversation_id, prompt)
                result = {
                    "conversation_id": conversation_id,
                    "user_message": {"id": user_message_id, "role": "user", "content": prompt},
                    "assistant_message": {"id": assistant_message_id, "role": "assistant", "content": response_text, "image_path": str(image_path)},
                }
            except Exception as error:
                error_text = f"I could not create that image: {error}"
                assistant_message_id = add_message(conversation_id, "assistant", error_text)
                result = {
                    "conversation_id": conversation_id,
                    "user_message": {"id": user_message_id, "role": "user", "content": prompt},
                    "assistant_message": {"id": assistant_message_id, "role": "assistant", "content": error_text},
                }
            resources["results"].put(result)
            resources["state"]["current"] = None

    thread = threading.Thread(target=run_worker, name="image-generation-worker", daemon=True)
    thread.start()
    resources["thread"] = thread
    return resources


def enqueue_prompt(prompt: str) -> int:
    """Put a prompt on the background queue; returns how many are now waiting."""
    resources = worker()
    resources["jobs"].put(
        {
            "id": uuid.uuid4().hex,
            "prompt": prompt.strip(),
            "conversation_id": st.session_state.active_conversation_id,
            "api_key": (st.session_state.get("api_key") or "").strip(),
            "model": st.session_state.image_model,
            "size": st.session_state.image_size,
        }
    )
    return resources["jobs"].qsize()


def list_waiting_jobs() -> list[dict[str, Any]]:
    return list(worker()["jobs"].queue)


def remove_waiting_job(job_id: str) -> None:
    jobs = worker()["jobs"]
    remaining = [job for job in list(jobs.queue) if job["id"] != job_id]
    with jobs.mutex:
        jobs.queue.clear()
        jobs.queue.extend(remaining)


def clear_waiting_jobs() -> None:
    jobs = worker()["jobs"]
    with jobs.mutex:
        jobs.queue.clear()


def render_generation_status() -> None:
    """Small live status line: what is generating and what is waiting."""
    resources = worker()
    current = resources["state"].get("current")
    waiting_jobs = list_waiting_jobs()
    lines: list[str] = []
    if current:
        preview = current[:60] + ("..." if len(current) > 60 else "")
        waiting_text = f" · {len(waiting_jobs)} waiting" if waiting_jobs else ""
        lines.append(f":orange[:material/progress_circle:] Generating image: {preview}{waiting_text}")
    for index, job in enumerate(waiting_jobs[:3], start=1):
        preview = job["prompt"][:60] + ("..." if len(job["prompt"]) > 60 else "")
        lines.append(f":gray[**{index}.** {preview}]")
    if len(waiting_jobs) > 3:
        lines.append(f":gray[... and {len(waiting_jobs) - 3} more]")
    if lines:
        st.caption("  \n".join(lines))


@st.fragment(run_every="1s")
def generation_mirror() -> None:
    """UI mirror of the background worker.

    A pure listener: it appends finished images to the open conversation and
    shows live progress. It never touches the generation itself, so no UI
    action can interrupt or restart an in-flight image.
    """
    resources = worker()
    changed = False
    while True:
        try:
            result = resources["results"].get_nowait()
        except queue.Empty:
            break
        if result["conversation_id"] == st.session_state.active_conversation_id:
            st.session_state.messages.append(result["user_message"])
            st.session_state.messages.append(result["assistant_message"])
            changed = True
    if changed:
        st.rerun(scope="app")
    render_generation_status()


# ---------------------------------------------------------------------------
# Schedule flow: ＋ button → "How many images?" → one field per prompt → all
# of them join the queue and generate one after another.
# ---------------------------------------------------------------------------


def open_schedule_fields() -> None:
    st.session_state.schedule_stage = "fields"
    st.session_state.schedule_feedback = None


def add_counted_prompts() -> None:
    """＋ Add all button callback: queue every filled prompt field, in order."""
    count = int(st.session_state.get("schedule_count") or 0)
    prompts: list[str] = []
    for index in range(1, count + 1):
        value = (st.session_state.get(f"schedule_prompt_{index}") or "").strip()
        if value:
            prompts.append(value)
    if not (st.session_state.get("api_key") or "").strip():
        st.session_state.schedule_feedback = "Add your OpenAI API key in the sidebar first."
        return
    if not prompts:
        st.session_state.schedule_feedback = "Fill in at least one prompt before adding to the schedule."
        return
    for prompt in prompts:
        enqueue_prompt(prompt)
    for index in range(1, count + 1):
        key = f"schedule_prompt_{index}"
        if key in st.session_state:
            del st.session_state[key]
    st.session_state.schedule_stage = "count"
    st.session_state.schedule_feedback = (
        f"Scheduled {len(prompts)} prompt{'s' if len(prompts) != 1 else ''} — "
        "they will generate one after another, in order."
    )


def main() -> None:
    st.set_page_config(page_title="Canvas Chat", page_icon="C", layout="wide")
    initialize_database()
    ensure_active_conversation()

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;600;700&display=swap');
        :root { --ink: #17211b; --muted: #68736b; --mint: #dcefe4; --accent: #e26d45; --line: #dfe7e1; }
        html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
        h1, h2, h3 { font-family: 'Space Grotesk', sans-serif; color: var(--ink); }
        .hero { padding: 0.5rem 0 1.5rem; border-bottom: 1px solid var(--line); margin-bottom: 1.5rem; }
        .hero-kicker { color: var(--accent); font-size: .78rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
        .hero h1 { font-size: clamp(2rem, 4vw, 3.4rem); line-height: 1; margin: .35rem 0 .65rem; }
        .hero p { color: var(--muted); max-width: 620px; margin: 0; font-size: 1.05rem; }
        [data-testid="stSidebar"] { background: #f3f6f2; border-right: 1px solid var(--line); }
        .history-label { color: var(--muted); font-size: .75rem; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; margin: 1.25rem 0 .55rem; }
        .stButton > button { border-radius: 8px; border-color: var(--line); }
        .stChatMessage { border: 1px solid var(--line); border-radius: 12px; padding: .75rem 1rem; }
        .hint { color: var(--muted); font-size: .9rem; padding: 1rem 0 2rem; }
        </style>
        <div class="hero">
          <div class="hero-kicker">Canvas / image studio</div>
          <h1>Think it. Type it. See it.</h1>
          <p>A private image-making chat with searchable conversations and a complete local history.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### Canvas Chat")
        api_key = st.text_input("OpenAI API key", type="password", value=st.session_state.get("api_key", ""), help="Used only for this browser session; it is not written to the database.")
        st.session_state.api_key = api_key
        st.divider()
        if st.button("+ New conversation"):
            conversation_id = create_conversation()
            load_conversation(conversation_id)
            st.rerun()

        st.markdown('<div class="history-label">History</div>', unsafe_allow_html=True)
        for conversation in list_conversations():
            label = conversation["title"] or "Untitled session"
            if st.button(label, key=f"conversation-{conversation['id']}"):
                load_conversation(conversation["id"])
                st.rerun()

        st.divider()

        # Schedule panel: ＋ → choose how many → one field per prompt → add all.
        st.markdown('<div class="history-label">Schedule prompts</div>', unsafe_allow_html=True)
        if st.session_state.get("schedule_stage") == "fields":
            st.number_input(
                "How many images do you want to schedule?",
                min_value=1,
                max_value=50,
                value=10,
                step=1,
                key="schedule_count",
            )
            count = int(st.session_state.schedule_count)
            st.caption(f"One prompt per image — fill in your {count} prompt{'s' if count != 1 else ''}:")
            for index in range(1, count + 1):
                st.text_input(f"Prompt {index}", key=f"schedule_prompt_{index}", placeholder=f"Describe image {index}...")
            add_col, cancel_col = st.columns([3, 1])
            add_col.button("＋ Add all to schedule", type="primary", width="stretch", on_click=add_counted_prompts)
            if cancel_col.button("✕", help="Back", width="stretch"):
                st.session_state.schedule_stage = "count"
        else:
            st.caption("Schedule several images at once — they generate one after another.")
            st.button("＋ Add to schedule", type="primary", width="stretch", on_click=open_schedule_fields)

        if st.session_state.get("schedule_feedback"):
            st.caption(f":gray[{st.session_state.schedule_feedback}]")
            st.session_state.schedule_feedback = None

        # Waiting prompts, with × to remove one or Clear schedule to empty all.
        waiting_jobs = list_waiting_jobs()
        if waiting_jobs:
            st.caption(f"{len(waiting_jobs)} prompt{'s' if len(waiting_jobs) != 1 else ''} waiting — generated one by one, in order.")
            for index, job in enumerate(waiting_jobs):
                col_prompt, col_remove = st.columns([4, 1])
                preview = job["prompt"][:34] + ("..." if len(job["prompt"]) > 34 else "")
                col_prompt.markdown(f"**{index + 1}.** {preview}")
                if col_remove.button(":material/close:", key=f"remove-scheduled-{job['id']}", help="Remove this prompt from the schedule"):
                    remove_waiting_job(job["id"])
                    st.rerun()
            if st.button("Clear schedule", width="stretch"):
                clear_waiting_jobs()
                st.rerun()
        else:
            st.caption("Nothing scheduled yet.")

        st.caption("Your key stays in session memory. Conversations and generated images stay in this app folder.")

    col1, col2 = st.columns([3, 1])
    with col2:
        st.selectbox("Image size", ["1024x1024", "1536x1024", "1024x1536"], key="image_size")
        st.selectbox("Image model", ["gpt-image-1"], key="image_model")
        st.caption("Scheduled images are saved to the conversation that was active when you scheduled them.")

    with col1:
        for message in st.session_state.messages:
            render_message(message)
        if not st.session_state.messages:
            st.markdown('<div class="hint">Describe an image below, or press ＋ Add to schedule in the sidebar. Try: “A glass greenhouse on Mars at blue hour, editorial photography.”</div>', unsafe_allow_html=True)

        # Live status + mirror of the background worker (refreshes itself).
        generation_mirror()

        prompt = st.chat_input("Describe the image you want to create...")
        if prompt:
            if not api_key:
                st.error("Add your OpenAI API key in the sidebar before creating an image.")
                st.stop()
            position = enqueue_prompt(prompt)
            st.toast(f"Added to the schedule — {position} prompt{'s' if position != 1 else ''} waiting.", icon="🗓️")
            st.rerun()


if __name__ == "__main__":
    main()
