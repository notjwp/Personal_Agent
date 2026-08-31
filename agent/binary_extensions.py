"""Binary file extensions to skip for text-based operations.

VENDORED from Hermes Agent (tools/binary_extensions.py), MIT, Copyright (c) 2025
Nous Research. Taken verbatim rather than re-derived: the value here IS the list,
and a hand-written one would be shorter and wrong. See NOTICE.

Why it earns its place, measured before lifting: read_file on an 8-byte PNG header
plus 2 KB of bytes returned 2,496 characters of mojibake into the model's context.
Their second half - the opaque-document set - names a hazard we also have, where a
model reads report.docx as extracted text and writes it back as plain text,
destroying the document.
"""

BINARY_EXTENSIONS = frozenset({
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif",
    # Videos
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".m4v", ".mpeg", ".mpg",
    # Audio
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma", ".aiff", ".opus",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz", ".z", ".tgz", ".iso",
    # Executables/binaries
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".obj", ".lib",
    ".app", ".msi", ".deb", ".rpm",
    # Documents (exclude .pdf — text-based, agents may want to inspect)
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Bytecode / VM artifacts
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear", ".node", ".wasm", ".rlib",
    # Database files
    ".sqlite", ".sqlite3", ".db", ".mdb", ".idx",
    # Design / 3D
    ".psd", ".ai", ".eps", ".sketch", ".fig", ".xd", ".blend", ".3ds", ".max",
    # Flash
    ".swf", ".fla",
    # Lock/profiling data
    ".lockb", ".dat", ".data",
})


def has_binary_extension(path: str) -> bool:
    """Check if a file path has a binary extension. Pure string check, no I/O."""
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:].lower() in BINARY_EXTENSIONS


# Container document formats (OOXML zip / OLE compound / ODF zip / EPUB zip / RTF)
# that a plain-text write can NEVER produce validly.  read_file auto-extracts
# these to readable text (via anydoc for the non-built-in formats), so a model
# that "read" report.docx and then writes the edited text back via
# write_file/patch silently destroys the document.
# PDF is intentionally NOT here: raw PDF syntax is text-authorable, so
# new-file creation is legitimate — only overwrites are dangerous (handled
# separately by the write guard).
OPAQUE_DOCUMENT_EXTENSIONS = frozenset({
    ".doc", ".docx", ".docm",
    ".xls", ".xlsx", ".xlsm", ".xlsb",
    ".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm",
    ".odt", ".ods", ".odp",
    ".rtf", ".epub",
})


def has_opaque_document_extension(path: str) -> bool:
    """True when the path names an opaque container document (.docx etc.).

    Pure string check, no I/O.
    """
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:].lower() in OPAQUE_DOCUMENT_EXTENSIONS


def is_pdf_path(path: str) -> bool:
    """True when the path has a .pdf extension. Pure string check, no I/O."""
    return path.lower().endswith(".pdf")
