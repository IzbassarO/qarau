"""Real agents. Every pay-per-use model call goes through llm_call (budget + cost log)."""

import json

import anthropic

from devteam import costs


def llm_call(
    team,
    role: str,
    task: str,
    system: str,
    prompt: str,
    schema: dict,
    max_tokens: int = 16000,
    client=None,
) -> dict:
    """One structured-output call for `role` (a key of config.models). Reserves the
    worst-case cost first, settles to the real usage after. No SDK retries: a retry is a new
    call with its own reservation (`devteam resume` re-runs the node)."""
    m = team.cfg["models"][role]
    if m["provider"] != "anthropic":
        raise NotImplementedError(f"provider {m['provider']!r} for {role}: only anthropic so far")
    # Every token is at least one byte, so UTF-8 bytes bound the input tokens from above.
    est_in = len((system + prompt + json.dumps(schema)).encode()) + 1000
    max_usd = (est_in * m["price_in"] + max_tokens * m["price_out"]) / 1e6
    rid = costs.reserve(team.home, team.cfg["daily_budget_usd"], role, m["model"], task, max_usd)
    if client is None:
        if not team.secrets.get("ANTHROPIC_API_KEY"):
            costs.release(team.home, rid)
            raise RuntimeError("ANTHROPIC_API_KEY is missing in devteam/.env")
        client = anthropic.Anthropic(api_key=team.secrets["ANTHROPIC_API_KEY"])
    if hasattr(client, "with_options"):
        client = client.with_options(max_retries=0)  # one reservation = one request
    try:
        r = client.messages.create(
            model=m["model"],
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": m["effort"],
                "format": {"type": "json_schema", "schema": schema},
            },
        )
    except anthropic.APIStatusError as e:
        if e.status_code < 500:  # rejected before any generation: nothing billed
            costs.release(team.home, rid)
        raise  # 5xx / connection / timeout: billing unknown, the reservation stays at max
    u = r.usage
    tokens_in = (
        u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
    )  # ponytail: cache priced as plain input
    usd = (tokens_in * m["price_in"] + u.output_tokens * m["price_out"]) / 1e6
    costs.settle(team.home, rid, tokens_in, u.output_tokens, usd)
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"{role} stopped with {r.stop_reason}; no usable answer")
    return json.loads(next(b.text for b in r.content if b.type == "text"))


class RealAgents:
    """v1: brain + dev_pipeline + tester. Not built yet (waiting for the owner's go)."""

    def plan(self, team, todo, feedback=None):
        raise NotImplementedError("real brain comes in v1")

    def develop(self, team, s):
        raise NotImplementedError("real developers come in v1/v2")

    def judge(self, team, s, checks):
        raise NotImplementedError("real tester comes in v1")
