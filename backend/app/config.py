"""All runtime configuration, read once from the environment."""
import os

# Ollama server and the local model the agent talks to.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("MODEL", "llama3.1")

# Safety cap on how many actions the agent may take for a single task.
MAX_STEPS = int(os.environ.get("MAX_STEPS", "15"))
