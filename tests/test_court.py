import json
import unittest

from smallclaims.court import Trial
from smallclaims.evidence import EvidenceLog
from smallclaims.mock import MockOtari
from smallclaims.otari import OtariClient
from smallclaims.server import resolve_cast


def run(claim, **kw):
    log = EvidenceLog(MockOtari())
    trial = Trial(OtariClient(api_key="otk_secret", transport=log), claim, "visitor-alice", **kw)
    events = list(trial.run())
    return events, log


def of(events, kind):
    return [e for e in events if e["type"] == kind]


class CourtTest(unittest.TestCase):
    def test_goldfish_injection_is_excluded_and_never_reaches_the_judge(self):
        events, log = run("Goldfish only have a three-second memory.")
        excluded = [e for e in of(events, "screened") if e["status"] == "excluded"]
        self.assertEqual([e["id"] for e in excluded], ["D2"])
        verdict = of(events, "verdict")[0]
        self.assertEqual(verdict["ruling"], "FALSE")
        judge_calls = [c for c in log.entries if (c["request"].get("metadata") or {}).get("step") == "judge"]
        self.assertTrue(judge_calls)
        self.assertNotIn("otk_secret", json.dumps(log.dump()))
        f = log.features()
        for name in ("web search", "guardrails", "multiple models, distinct roles",
                     "per-user (end-user) attribution"):
            self.assertTrue(f[name], name)

    def test_otari_case_substitute_counsel_and_case_files(self):
        events, _ = run("Otari can route to more than 40 LLM providers through one endpoint.")
        sub = of(events, "substitute")[0]
        self.assertEqual((sub["failed"], sub["to"]),
                         ("deepseek/deepseek-v4-pro-0813", "moonshot/kimi-k3"))
        self.assertEqual(of(events, "records")[0]["server"], "deepwiki")
        self.assertEqual(of(events, "verdict")[0]["models"]["defense"], "kimi-k3")

    def test_venus_public_defender_and_clerk_sandbox(self):
        events, log = run("A day on Venus is longer than its year.")
        self.assertEqual(len(of(events, "downgrade")), 1)
        clerk = of(events, "clerk")[0]
        self.assertTrue(clerk["sandbox"])
        self.assertIn("18.3", clerk["stdout"])
        self.assertTrue(of(events, "verdict")[0]["downgraded"])
        self.assertTrue(log.features()["code execution"])

    def test_fee_cap_adjourns_with_provisional_ruling(self):
        events, _ = run("A day on Venus is longer than its year.", fee_cap=0.03)
        self.assertEqual(len(of(events, "adjourned")), 1)
        self.assertTrue(of(events, "verdict")[0]["provisional"])

    def test_linked_page_with_injection_is_excluded_before_counsel_reads_it(self):
        events, log = run("https://example.org/suspicious-page")
        first = of(events, "screened")[0]
        self.assertEqual((first["id"], first["status"]), ("A", "excluded"))
        counsel = [c for c in log.entries
                   if (c["request"].get("metadata") or {}).get("step") in ("prosecution", "defense")]
        self.assertTrue(counsel)
        self.assertEqual(of(events, "verdict")[0]["excluded"][0]["id"], "A")

    def test_every_call_names_the_visitor(self):
        _, log = run("Goldfish only have a three-second memory.")
        chats = [c for c in log.entries if c["call"].endswith("/chat/completions")]
        self.assertTrue(chats)
        self.assertTrue(all(c["request"]["user"] == "visitor-alice" for c in chats))

    def test_resolve_cast_keeps_policies_and_fills_by_family(self):
        cast = resolve_cast({"judge": ["court-judge"], "defense": ["nope/missing"]},
                            ["deepseek/deepseek-v4-flash", "moonshot/kimi-k3"])
        self.assertEqual(cast["judge"], ["court-judge"])
        self.assertEqual(cast["defense"], ["deepseek/deepseek-v4-flash", "moonshot/kimi-k3"])


if __name__ == "__main__":
    unittest.main()
