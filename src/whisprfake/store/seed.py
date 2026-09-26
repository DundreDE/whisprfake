"""Starter dictionary of developer/tech terms. They bias the ASR (Whisper prompt), are given to the LLM as
preferred spellings and drive the phonetic post-correction. Edit with `whisprfake ctl dictionary.add`."""

TECH_TERMS: list[tuple[str, list[str]]] = [
    ("Omarchy", ["oh marchy", "omarchie"]), ("Hyprland", ["hyper land", "hyperland"]), ("Arch Linux", []),
    ("Wayland", []), ("PipeWire", ["pipe wire"]), ("systemd", ["system d"]), ("ROCm", ["rock m", "rockm"]),
    ("Vulkan", []), ("Ollama", ["o llama", "olama"]), ("Qwen", ["quen", "kwen"]), ("Whisper", []),
    ("llama.cpp", ["llama cpp", "lama cpp"]), ("Claude", ["clawed", "cloud code"]), ("Claude Code", []),
    ("Anthropic", []), ("OpenAI", []), ("ChatGPT", ["chat gpt"]), ("GitHub", ["git hub", "get hub"]),
    ("GitLab", []), ("TypeScript", ["type script"]), ("JavaScript", ["java script"]), ("Python", []),
    ("Rust", []), ("Next.js", ["next js", "next jay es"]), ("Node.js", ["node js"]), ("React", []),
    ("Tailwind", ["tail wind"]), ("Vercel", ["versel", "wercel"]), ("Supabase", ["super base", "supa base"]),
    ("PostgreSQL", ["postgres ql", "post gress"]), ("SQLite", ["sequel lite", "sql lite"]), ("Docker", []),
    ("Kubernetes", ["kuber netis", "kubernetis"]), ("API", []), ("JSON", ["jason"]), ("YAML", ["yaml"]),
    ("npm", []), ("pnpm", []), ("uv", []), ("Neovim", ["neo vim"]), ("VS Code", ["vs code", "v s code"]),
    ("Cursor", []), ("LLM", []), ("MCP", []), ("GPU", []), ("CPU", []), ("Radeon", []),
]


def seed(store) -> None:
    if store.q("SELECT 1 FROM dictionary LIMIT 1"):
        return
    for term, sounds in TECH_TERMS:
        store.add_term(term, sounds)
