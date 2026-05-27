from dataclasses import dataclass


@dataclass(frozen=True)
class ExpertResult:
    role_name: str
    content: str


class ExpertTeam:
    async def run(self, message: str) -> list[ExpertResult]:
        return [
            ExpertResult(role_name="expert_a", content=f"Primary analysis placeholder for: {message}"),
            ExpertResult(role_name="expert_b", content="Critique and supplement placeholder."),
            ExpertResult(role_name="synthesizer", content="Final synthesis placeholder."),
        ]

