import sys

try:
    from .app import main
except ImportError as exc:  # usually: started with a Python that lacks the dependencies
    sys.stderr.write(
        f"Missing dependency: {exc.name}\n"
        f"Interpreter: {sys.executable}\n"
        "Use the project environment:\n"
        "  .venv\\Scripts\\python -m story_agent.web\n"
        "or install the dependencies first:  pip install -r requirements.txt\n"
    )
    sys.exit(1)

if __name__ == "__main__":
    main()
