
"""
CodeForge Agents — Bring-Your-Own Knowledge (session-scoped)
============================================================
Lets a user add their OWN notes / standards for the CURRENT session only.

WHY EPHEMERAL + PER SESSION:
The baked `codeforge_knowledge` collection is process-global and, in
Kubernetes, per-pod. User uploads must not leak into another user's
retrieval. Each browser session therefore gets its own in-memory
ChromaDB collection.

It reuses the SAME chunker and embedder as the baked index, so all
vectors live in one embedding space.

Stored in st.session_state (per session), NEVER @st.cache_resource.
"""

import hashlib
import uuid

import streamlit as st

from rag.chunking import chunk_document
from rag.embedder import embed, available

MAX_FILE_BYTES = 1_000_000
MAX_SESSION_CHUNKS = 200

_COLL_KEY = "user_kb"
_SEEN_KEY = "user_kb_seen"
_DOCS_KEY = "user_kb_docs"
_CLIENT_KEY = "user_kb_client"
_NAME_KEY = "user_kb_collection_name"


def _collection():
    """Return this session's own ephemeral collection, creating it once."""

    if _COLL_KEY not in st.session_state:
        import chromadb
        from chromadb.config import Settings

        # Keep the client in session state so the collection's
        # in-memory database remains available during Streamlit reruns.
        if _CLIENT_KEY not in st.session_state:
            st.session_state[_CLIENT_KEY] = chromadb.EphemeralClient(
                settings=Settings(anonymized_telemetry=False)
            )

        client = st.session_state[_CLIENT_KEY]

        # A unique name prevents collisions with collections left
        # behind by previous sessions or calls to clear().
        if _NAME_KEY not in st.session_state:
            st.session_state[_NAME_KEY] = (
                f"user_knowledge_{uuid.uuid4().hex}"
            )

        collection_name = st.session_state[_NAME_KEY]

        # Safe if the collection already exists.
        st.session_state[_COLL_KEY] = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        st.session_state[_SEEN_KEY] = set()
        st.session_state[_DOCS_KEY] = []

    return st.session_state[_COLL_KEY]


def collection_or_none():
    """Return the session collection if it contains documents, else None."""

    coll = st.session_state.get(_COLL_KEY)

    try:
        return coll if coll is not None and coll.count() > 0 else None
    except Exception:
        return None


def docs() -> list[dict]:
    """Return the list of documents added during this session."""
    return st.session_state.get(_DOCS_KEY, [])


def _chunks_used() -> int:
    """Return the number of chunks currently stored in this session."""
    coll = st.session_state.get(_COLL_KEY)

    try:
        return coll.count() if coll is not None else 0
    except Exception:
        return 0


def add_text(name: str, raw) -> dict:
    """
    Add one document from uploaded bytes or pasted text.

    A content hash prevents identical text from being indexed twice
    during Streamlit reruns.
    """

    if not available():
        return {
            "ok": False,
            "message": "Embedding model unavailable — knowledge upload is disabled."
        }

    # Decode uploaded bytes safely.
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_FILE_BYTES:
            return {
                "ok": False,
                "message": f"{name} is larger than 1 MB — please trim it."
            }

        text = bytes(raw).decode("utf-8-sig", errors="replace")
    else:
        text = str(raw)

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    if not text:
        return {
            "ok": False,
            "message": f"No readable text found in {name}."
        }

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]

    coll = _collection()

    # Avoid indexing the same content again.
    if digest in st.session_state[_SEEN_KEY]:
        return {
            "ok": True,
            "duplicate": True,
            "message": f"{name} is already added."
        }

    chunks = chunk_document(text, source=name)

    if not chunks:
        return {
            "ok": False,
            "message": f"No readable text found in {name}."
        }

    budget = MAX_SESSION_CHUNKS - _chunks_used()

    if budget <= 0:
        return {
            "ok": False,
            "message": (
                f"Session knowledge is full ({MAX_SESSION_CHUNKS} chunks). "
                "Clear it to add more."
            )
        }

    truncated = len(chunks) > budget

    if truncated:
        chunks = chunks[:budget]

    texts = [chunk["text"] for chunk in chunks]

    try:
        embeddings = embed(texts)

        # Namespace IDs by content hash to prevent collisions.
        coll.add(
            ids=[f"{digest}:{chunk['id']}" for chunk in chunks],
            documents=texts,
            embeddings=embeddings,
            metadatas=[
                {
                    "source": name,
                    "section": chunk["section"],
                    "language": "user",
                    "origin": "user"
                }
                for chunk in chunks
            ],
        )

    except Exception as exc:
        return {
            "ok": False,
            "message": f"Could not index {name}: {exc}"
        }

    st.session_state[_SEEN_KEY].add(digest)

    st.session_state[_DOCS_KEY].append({
        "name": name,
        "chunks": len(chunks)
    })

    message = f"{name} → {len(chunks)} chunk(s) indexed."

    if truncated:
        message += " (truncated to fit the session limit)"

    return {
        "ok": True,
        "message": message
    }


def clear():
    """Clear this session's uploaded knowledge."""

    # Remove the session's collection from its ephemeral database.
    client = st.session_state.get(_CLIENT_KEY)
    collection_name = st.session_state.get(_NAME_KEY)

    if client is not None and collection_name is not None:
        try:
            client.delete_collection(name=collection_name)
        except Exception:
            pass

    # Remove session references so a fresh collection is created next time.
    for key in (
        _COLL_KEY,
        _SEEN_KEY,
        _DOCS_KEY,
        _CLIENT_KEY,
        _NAME_KEY
    ):
        st.session_state.pop(key, None)
