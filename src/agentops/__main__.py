"""`python -m agentops`: the same command line as the `agentops` script."""

from agentops.cli import app

if __name__ == "__main__":
    app(prog_name="agentops")
