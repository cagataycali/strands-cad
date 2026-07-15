"""Quickstart — spin up an agent with all strands-cad tools."""
from strands import Agent
from strands_cad import ALL_TOOLS

agent = Agent(
    tools=ALL_TOOLS,
    system_prompt=(
        "You are a 3D-printing assistant with atomic CAD/mesh/slice/print tools. "
        "Compose the tools to accomplish the user's goal. "
        "Every tool does one job — chain them yourself."
    ),
)

if __name__ == "__main__":
    # Example: "Analyze the STL at /tmp/part.stl and estimate its PLA weight."
    import sys
    q = " ".join(sys.argv[1:]) or "List every tool you have and one-line what each does."
    print(agent(q))
