"""Route Intelligence agent: an LLM tool loop when a key is configured, otherwise an offline planner.

Both planners use the same tools, and every call is recorded in the reasoning trace.
The core workflow ("triage the fleet") replaces the manual work the brief describes:
evaluating alternative routes and recalculating ETAs across dozens of vehicles.
"""
from __future__ import annotations

import json
import re

from . import llm, tools
from .config import (GROQ_API_KEY, GROQ_MODEL, OLLAMA_BASE_URL, OLLAMA_MODEL, OPENAI_API_KEY, OPENAI_MODEL)

SYSTEM_PROMPT = """You are a route-intelligence agent for a logistics operations team (DataCo shipments:
trucks on US/Canadian roads and international air freight). Workflow for disruption questions:
 1. scan_fleet to find shipments that will breach their SLA or have a cheaper option,
 2. for each one: predict_delay_risk, check_weather, check_incidents, check_customs (international),
 3. get_route_options to compare recalculated ETAs, SLA status and total cost,
 4. propose_reroute for dispatcher approval when the best option differs (never claim it is done),
 5. draft_customer_notice when the delivery will still be late.
If the user reports a disruption (e.g. "road closed near Denver"), call report_disruption first.
Answer in under 200 words with shipment ids, ETAs, SLA status and dollar figures."""

MAX_LLM_STEPS = 10
TRIAGE_MAX = 6


def _has(q: str, *phrases: str) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", q) for p in phrases)


class _Trace:
    def __init__(self):
        self.steps = []

    def call(self, thought: str, tool: str, args: dict) -> dict:
        obs = tools.execute_tool(tool, args)
        self.steps.append({"thought": thought, "tool": tool, "args": args, "observation": obs})
        return obs


def _money(x: float) -> str:
    return f"${x:,.0f}"


def triage(trace: _Trace, limit: int = TRIAGE_MAX) -> str:
    scan = trace.call("Evaluate every active shipment against live weather, incidents and customs.", "scan_fleet", {})
    if scan.get("error"):
        return f"Fleet scan failed: {scan['error']}"
    c = scan["sla_counts"]
    lines = [f"Scanned {scan['shipments']} shipments: {c['BREACH']} predicted SLA breaches, {c['AT_RISK']} at risk, "
             f"{c['ON_TIME']} on time. {scan['reroute_opportunities']} have a better option "
             f"(total potential saving {_money(scan['potential_saving'])})."]
    targets = scan["at_risk"][:limit]
    if not targets:
        return lines[0] + " No action needed."
    for t in targets:
        sid = t["id"]
        risk = trace.call(f"{sid} is {t['sla']}: check its historical late risk.", "predict_delay_risk", {"shipment_id": sid})
        if t["kind"] == "international":
            trace.call(f"Check customs clearance for {sid}.", "check_customs", {"shipment_id": sid})
        else:
            trace.call(f"Check live weather along {sid}'s routes.", "check_weather", {"shipment_id": sid})
            trace.call(f"Check traffic incidents on {sid}'s routes.", "check_incidents", {"shipment_id": sid})
        opts = trace.call(f"Recalculate ETA, SLA and cost for every option of {sid}.", "get_route_options", {"shipment_id": sid})
        cur = next((o for o in opts.get("options", []) if o["option"] == opts.get("current_option")), None)
        best = next((o for o in opts.get("options", []) if o["option"] == opts.get("recommended")), None)
        if not cur or not best:
            lines.append(f"• {sid}: could not evaluate options ({opts.get('error', 'no options')}).")
            continue
        why = ", ".join(i["kind"].replace("_", " ") for i in cur["incidents"]) or (cur["weather"]["worst"] or "service level")
        text = (f"• {sid} → {t['dest']} [{cur['sla']['status']}] ETA {cur['eta'].replace('T', ' ')} vs promised "
                f"{t['promised_at'].replace('T', ' ')} ({why}; ML late risk {risk.get('late_probability', 0):.0%}).")
        if opts["recommend_change"]:
            prop = trace.call(f"Propose switching {sid} to {best['option']} for dispatcher approval.", "propose_reroute",
                              {"shipment_id": sid, "option": best["option"]})
            cmp = opts.get("comparison_with_recommended") or {}
            sooner = -cmp.get("eta_change_h", 0)
            timing = f"arrives {sooner:g} h sooner, " if sooner > 0 else f"arrives {-sooner:g} h later, " if sooner < 0 else ""
            text += (f" Proposed: {best['label']} → ETA {best['eta'].replace('T', ' ')} ({best['sla']['status']}), "
                     f"{timing}saves {_money(opts['saving'])}. Decision #{prop.get('decision_id')} awaiting approval.")
        else:
            text += " Current plan is already the cheapest option."
        if (best if opts["recommend_change"] else cur)["sla"]["status"] != "ON_TIME":
            notice = trace.call(f"{sid} will still miss or be close to its promise: draft a customer notice.",
                                "draft_customer_notice", {"shipment_id": sid})
            if notice.get("drafted"):
                text += f" Customer delay notice #{notice['notification_id']} drafted."
        lines.append(text)
    if len(scan["at_risk"]) > limit:
        lines.append(f"{len(scan['at_risk']) - limit} more flagged shipments are listed on the Shipments tab.")
    return "\n".join(lines)


def _parse_disruption(q: str) -> dict | None:
    kind = ("road_closure" if _has(q, "closed", "closure", "accident", "crash", "blocked")
            else "storm" if _has(q, "storm", "snow", "blizzard", "hurricane", "flood", "hail")
            else "congestion" if _has(q, "congestion", "traffic", "jam")
            else "customs_hold" if _has(q, "customs", "border") else None)
    if not kind:
        return None
    m = re.search(r"\b(?:near|in|at|around)\s+([A-Za-zÀ-ÿ .'-]+?)(?:[,.?!]|$)", q, re.IGNORECASE)
    if not m:
        return None
    place = m.group(1).strip()
    if kind == "customs_hold":
        return {"kind": kind, "country": place.title()}
    return {"kind": kind, "city": place.title()}


def _deterministic(query: str) -> dict:
    q = query.lower()
    trace = _Trace()

    def done(answer: str) -> dict:
        return {"steps": trace.steps, "answer": answer, "backend": "offline-router"}

    sid = tools.norm_id(query)
    reported = _parse_disruption(q) if _has(q, "report", "there is", "there's", "closed", "closure", "storm",
                                             "congestion", "customs", "accident", "crash", "jam", "blocked") else None
    if reported and not sid:
        obs = trace.call(f"Log the reported disruption ({reported['kind']}).", "report_disruption", reported)
        if obs.get("error"):
            return done(f"Could not log the disruption: {obs['error']}")
        answer = triage(trace)
        return done(f"Logged {reported['kind'].replace('_', ' ')} near {obs.get('near') or obs.get('country')} "
                    f"(incident #{obs['incident_id']}).\n" + answer)

    if sid:
        if _has(q, "weather", "rain", "snow", "wind"):
            w = trace.call(f"Live weather along {sid}'s route.", "check_weather", {"shipment_id": sid})
            if w.get("error"):
                return done(w["error"])
            worst = w.get("conditions", [{}])[0] if w.get("conditions") else None
            return done(f"{sid}: " + (f"worst forecast on the current route is {worst['condition']} around "
                                      f"{worst['time'].replace('T', ' ')} UTC (speed penalty {w['penalty']:.0%})."
                                      if worst else "no significant weather on the route in the next 48 h.")
                        + f" Source: {w.get('source')}.")
        if _has(q, "customs", "clearance", "border"):
            c = trace.call(f"Customs status for {sid}.", "check_customs", {"shipment_id": sid})
            if c.get("error") or not c.get("applies"):
                return done(c.get("error") or c.get("note"))
            holds = "; ".join(f"{h['title']} (+{h['extra_h']:.0f} h)" for h in c["active_holds"]) or "none"
            return done(f"{sid} → {c['destination']}: typical clearance {c['typical_clearance_h']} h; active holds: {holds}.")
        if _has(q, "reroute", "re-route", "propose", "switch", "fix"):
            m = re.search(r"\bto\s+(R\d+|standard|expedited[_ ]broker|express[_ ]air)\b", query, re.I)
            target = (m.group(1).upper() if m.group(1)[0] in "rR" else m.group(1).lower().replace(" ", "_")) if m else None
            args = {"shipment_id": sid, **({"option": target} if target else {})}
            p = trace.call(f"Propose {target or 'the best option'} for {sid}.", "propose_reroute", args)
            if p.get("error"):
                return done(p["error"])
            if p["status"] == "NO_CHANGE":
                return done(f"{sid}: that is already the current plan." if target else
                            f"{sid}: the current plan is already the best option.")
            cmp = p.get("comparison") or {}
            sooner = -cmp.get("eta_change_h", 0)
            timing = f", arrives {sooner:g} h sooner" if sooner > 0 else f", arrives {-sooner:g} h later" if sooner < 0 else ""
            gain = f"saves {_money(p['saving'])}" if p["saving"] >= 0 else f"costs {_money(-p['saving'])} more"
            return done(f"{sid}: proposed {p['label']} (ETA {p['eta_after'].replace('T', ' ')}, {p['sla_after']}{timing}, "
                        f"{gain}). Decision #{p['decision_id']} is {p['status']}. Reason: {p['reason']}.")
        if _has(q, "notify", "notice", "customer", "email"):
            n = trace.call(f"Draft a customer notice for {sid}.", "draft_customer_notice", {"shipment_id": sid})
            return done(n.get("error") or n.get("message") or n.get("note"))
        # default: full analysis of one shipment
        trace.call(f"Shipment details for {sid}.", "get_shipment", {"shipment_id": sid})
        risk = trace.call("Historical late risk from the ML model.", "predict_delay_risk", {"shipment_id": sid})
        opts = trace.call("Recalculate ETA, SLA and cost for every option.", "get_route_options", {"shipment_id": sid})
        if opts.get("error"):
            return done(opts["error"])
        parts = [f"{sid}: ML late risk {risk.get('late_probability', 0):.0%} (mode {risk.get('mode')}). Options:"]
        for o in opts["options"]:
            mark = " (current)" if o["option"] == opts["current_option"] else ""
            parts.append(f"  {o['option']}{mark}: {o['label']}, ETA {o['eta'].replace('T', ' ')}, {o['sla']['status']} "
                         f"(slack {o['sla']['slack_h']} h), cost {_money(o['cost']['total'])}"
                         + (", BLOCKED" if o["blocked"] else ""))
        parts.append(f"Recommendation: switch to {opts['recommended']} (saves {_money(opts['saving'])})."
                     if opts["recommend_change"] else "Recommendation: keep the current plan.")
        return done("\n".join(parts))

    if _has(q, "how many", "count", "number of") and _has(q, "breach", "breaches", "late", "risk", "at risk", "shipments"):
        scan = trace.call("Scan the fleet before counting.", "scan_fleet", {})
        if scan.get("error"):
            return done(f"Fleet scan failed: {scan['error']}")
        c = scan["sla_counts"]
        return done(f"{scan['shipments']} active shipments: {c['BREACH']} predicted SLA breaches, {c['AT_RISK']} at risk, "
                    f"{c['ON_TIME']} on time; {scan['reroute_opportunities']} reroute opportunities.")
    if _has(q, "incident", "incidents", "disruption", "disruptions"):
        obs = trace.call("List active incidents.", "check_incidents", {})
        items = obs.get("incidents", [])
        return done(f"{len(items)} active incidents: " + "; ".join(f"#{i['id']} {i['kind'].replace('_', ' ')}: {i['title']}"
                                                                   for i in items) if items else "No active incidents.")
    if _has(q, "triage", "scan", "what needs", "attention", "at risk", "plan", "reroute", "disrupted", "fleet",
            "breach", "breaches", "late", "delays", "optimi"):
        return done(triage(trace))
    return done("I can triage the whole fleet ('triage the fleet'), analyse a shipment ('TRK-020'), check its weather, "
                "incidents or customs, propose a reroute ('reroute TRK-020'), draft a customer notice, or log a "
                "disruption you report ('road closed near Denver').")


def _client():
    from openai import OpenAI
    prov = llm.backend_name()
    if prov == "groq":
        return prov, OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1"), GROQ_MODEL
    if prov == "openai":
        return prov, OpenAI(api_key=OPENAI_API_KEY), OPENAI_MODEL
    return prov, OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL), OLLAMA_MODEL


def _for_llm(obs: dict) -> str:
    slim = dict(obs)
    if "at_risk" in slim:
        slim["at_risk"] = slim["at_risk"][:12]
    return json.dumps(slim, default=str)[:7000]


def run_agent(query: str, client_factory=None) -> dict:
    if not llm.is_available() and client_factory is None:
        return _deterministic(query)
    try:
        prov, client, model = (client_factory or _client)()
    except Exception:
        return _deterministic(query)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}]
    steps = []
    try:
        for _ in range(MAX_LLM_STEPS):
            resp = client.chat.completions.create(model=model, messages=messages, tools=tools.OPENAI_TOOLS,
                                                  tool_choice="auto", temperature=0.2, max_tokens=800)
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                return {"steps": steps, "answer": msg.content or "", "backend": prov}
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": tc.id, "type": "function",
                                             "function": {"name": tc.function.name,
                                                          "arguments": tc.function.arguments or "{}"}} for tc in calls]})
            for tc in calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                obs = tools.execute_tool(tc.function.name, args)
                steps.append({"thought": msg.content or f"Calling {tc.function.name}.", "tool": tc.function.name,
                              "args": args, "observation": obs})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": _for_llm(obs)})
    except Exception as e:
        r = _deterministic(query)
        r["backend"] = f"offline-router (LLM failed: {type(e).__name__})"
        return r
    return {"steps": steps, "answer": "Stopped after the maximum number of tool steps.", "backend": prov}
