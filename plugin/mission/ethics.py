"""mission ethics — the Ethics Reviewer's gate on the mission and every goal (ROADMAP §4.12, M9.4).

Ethics is reviewed when the mission is set and each time a goal is derived, in three tiers:
  clear  proceed
  gray   pause and ask the human (aggressive notifications, collecting more user data, …);
         the consent is bound to the reviewed content's sha256 and to that review, stored
         inside the goal, and never carries over to another goal or a changed one
  hard   illegal, deceptive or harmful work: blocked, and no consent or later review unlocks it
The reviewer (a model) judges; this gate checks structure and evidence: a recorded review of
exactly the current content, every topic the fixed screen matched addressed by a finding,
the tier no lower than the worst finding, a screened hard-line topic never cleared outright,
and a later review never lowering the tier of the same content. A hard line holds the goal for
good, and the words of the gaps it is about (a hard finding in a goal of several gaps names
them: "gaps": [gap_id, …]), so no goal derived later takes them again.

Usage:
  mission ethics new --subject mission|<goal_id>
        write a review skeleton bound to the subject (ref, id, sha256) with the screen's topics;
        prints its path. The reviewer fills `findings` and `tier`, then records it.
  mission ethics record FILE
        validate and seal the review; emits ethics.reviewed (and ethics.blocked on a hard line).
        Exits like `check`.
  mission ethics check --subject mission|<goal_id> [--json]
        the gate. Exit 0 clear, or gray with consent · 2 no review of this content, an invalid
        or edited review, or a hard line · 3 gray without the human's consent. --json adds the
        facts, with state: none (review it) · broken (restore the recorded review) · pending
        (ask the human) · hard (retired) · ok · unresolved (no such subject)
  mission ethics consent --subject mission|<goal_id> --by NAME --reason TEXT
        the human's consent to a gray review of the current content (ethics.consent);
        refused on a hard line
  mission ethics screen --subject mission|<goal_id> | --text TEXT
        the screened topics as JSON {category: matched phrase}
"""
import hashlib
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin"))
import _events  # noqa: E402
import _mission as M  # noqa: E402

TIERS = ("clear", "gray", "hard")
HARD_LINE = ("illegal", "deceptive", "harmful")

# The screen: fixed patterns per topic. A match does not decide anything; it names a topic the
# review must address (a finding), and a matched hard-line topic can't be cleared outright.
# Blunt on purpose ("anti-fraud" matches "fraud"): the reviewer says why in one line, and a
# defensive use of a hard-line word still pauses once for the human (a false gray costs one consent;
# a false clear costs the gate). Labels and the mechanics a planner would write instead both count.
W = r"(?:[\w'-]+ )"   # one word of a gap, possessives and hyphens included (the text is normalized first)
CATEGORIES = {
    "illegal": [r"\bpira(?:te|tes|ted|ting|cy)\b", r"\bcounterfeit", r"\blaunder(?:s|ed|ing)?\b", r"\bfraud",
                r"\b(?:evade|evading|evasion of) (?:tax|taxes|sanctions)\b", r"\bunlicensed\b",
                r"\b(?:bypass|circumvent)\w* (?:the )?(?:drm|copy[- ]protection|paywalls?|licen[cs]e checks?)\b",
                r"\bcrack(?:ed|ing)? (?:software|licen[cs]es?|keys?)\b", r"\bkeygens?\b",
                r"\bscrap\w* .{0,40}\b(?:without (?:permission|consent)|against (?:the |their )?terms)\b"],
    "deceptive": [r"\bdark[- ]patterns?\b",
                  r"\bfake[- ]" + W + r"{0,2}(?:reviews?|ratings?|accounts?|followers?|testimonials?|news|urgency|"
                  r"scarcity|countdowns?|timers?|endorsements?|discounts?)\b",
                  r"\bimpersonat", r"\bphish", r"\bastroturf", r"\bmislead", r"\bdecepti", r"\bdeceiv",
                  r"\bhidden (?:fees?|charges?|costs?|subscriptions?)\b", r"\bbait[- ]and[- ]switch\b",
                  r"\bsock ?puppets?\b", r"\bclickbait\b", r"\b(?:secretly|covertly)\b", r"\bdeep ?fakes?\b",
                  r"\b(?:cancel|unsubscrib|opt[- ]out|delet)[\w-]* " + W + r"{0,3}(?:hard|harder|difficult) to find\b",
                  r"\b(?:hard|harder|difficult) to (?:cancel|unsubscribe|opt[- ]out|delete|leave)\b",
                  r"\b(?:hid(?:e|es|ing|den)|bur(?:y|ies|ying|ied)) (?:the |a |an )?(?:unsubscribe|cancel\w*|opt[- ]outs?|"
                  r"delete)\b",
                  r"\bpre-?(?:check|tick|select)\w* " + W + r"{0,5}?(?:box|boxes|opt[- ]ins?|consent|subscri\w*)\b",
                  r"\b(?:charge|bill)\w* " + W + r"{0,4}after (?:they|the user|users?|the customer|customers?) cancel"],
    "harmful": [r"\bmalware\b", r"\bransomware\b", r"\bspyware\b", r"\bstalk(?:s|ed|er|ers|ing)?\b", r"\bharass",
                r"\bdox(?:x)?(?:es|ed|ing)?\b", r"\bweapons?\b", r"\bself[- ]harm\b", r"\bkey ?loggers?\b",
                r"\bddos\b", r"\bbotnets?\b", r"\bexploit\w* (?:children|minors|users|people|the vulnerable)\b",
                r"\bhack\w* into\b", r"\bexfiltrat", r"\bbrute[- ]?forc\w* (?:the )?(?:passwords?|accounts?|logins?) of\b",
                r"\bcrypto[- ]?jack", r"\bmin(?:e|es|ing) (?:crypto\w*|bitcoins?|monero)\b"],
    "user-data": [r"\b(?:collect|harvest|gather|capture|store|record)\w* (?:more |extra |additional |all )?"
                  r"(?:of )?(?:the )?(?:user|users'?|personal|customer|people's) ?(?:data|information|info)\b",
                  r"\bpersonal (?:data|information|info)\b", r"\bpii\b",
                  r"\b(?:gps |geo)?location (?:data|tracking|history)\b",
                  r"\btrack\w* (?:the )?(?:users?|people|visitors?|location)\b",
                  r"\bcontacts? (?:lists?|books?)\b", r"\bbiometric", r"\bfingerprint(?:s|ing)?\b",
                  r"\b(?:email addresses|phone numbers)\b",
                  r"\b(?:collect|harvest|gather|capture|log|record|store|upload)\w* " + W + r"{0,3}(?:data|information|info) "
                  r"(?:about|on|from) (?:the )?(?:users?|people|customers?|visitors?|children|kids)\b",
                  r"\b(?:children|kids|minors)(?:'s|')? (?:data|information|info|locations?|photos?)\b",
                  r"\b(?:address books?|microphone|keystrokes?|clipboard|browsing history|private messages)\b"],
    "notifications": [r"\bnotif(?:y|ies|ied|ying|ication|ications)\b", r"\bpush (?:alerts?|messages?)\b",
                      r"\bnag(?:s|ging)?\b"],
    "engagement": [r"\bengagement\b", r"\btime[- ]on[- ](?:app|site|page)\b", r"\bstreaks?\b",
                   r"\binfinite scroll", r"\baddict", r"\bfomo\b", r"\bretention (?:hooks?|tricks?|loops?)\b",
                   r"\bgamif"],
    "third-party": [r"\bthird[- ]part(?:y|ies)\b",
                    r"\b(?:sell|share|monetiz)\w* " + W + r"{0,3}(?:data|information)\b",
                    r"\bad(?:vertising)? networks?\b", r"\badvertisers?\b", r"\btracking pixels?\b", r"\bdata brokers?\b", r"\bretarget"],
    "outreach": [r"\b(?:send|sends|sending|email|emails|emailing|message|messaging|text|texting|sms)"
                 r" (?:\w+ ){0,3}(?:users|customers|people|contacts|leads|subscribers)\b",
                 r"\bcold (?:emails?|outreach|calls?)\b", r"\bmass (?:emails?|messages?|mail)\b", r"\bnewsletters?\b",
                 r"\b(?:post|posts|posting|tweet\w*) on (?:their|users'?|someone'?s?|a user'?s) behalf\b",
                 r"\bauto[- ]?(?:post|dm|repl(?:y|ies))\w*\b", r"\bspam\w*\b"],
    "security": [r"\b(?:disable|disabling|remove|removing|turn off|turning off|bypass|bypassing|skip|skipping)"
                 r" (?:the )?(?:auth\w*|login|security|encryption|2fa|mfa|tls|https|csrf|rate limit\w*)\b",
                 r"\bplain ?text passwords?\b", r"\bstor\w* (?:the )?passwords?\b"],
}
_RX = {c: [re.compile(p, re.I) for p in ps] for c, ps in CATEGORIES.items()}

# top-level fields that are not the subject's content: outside its sha256 (M.UNHASHED: the seal,
# the status, the approvals and consent reasons), and created_at while it is a timestamp. Everything
# else is screened, keys and values, at any depth, except values that are harness ids or hashes.
SKIP = set(M.UNHASHED)
ID_VALUE = re.compile(r"^(?:[a-z]{3}_[0-9a-f]{20}|[0-9a-f]{64})$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
REASON_MAX = 200
APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u2032": "'"})


def strings(x, top=True):
    if isinstance(x, dict):
        for k, v in x.items():
            if top and (k in SKIP or k == "created_at" and isinstance(v, str) and TIMESTAMP.match(v)):
                continue
            yield str(k)
            yield from strings(v, False)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v, False)
    elif isinstance(x, str) and not ID_VALUE.match(x):
        yield x


def normalize(text):
    """What the screen reads: NFKC (fullwidth and compatibility forms folded), typographic apostrophes
    folded to ', invisible format characters (zero-width, soft hyphen) removed, each run of whitespace one space."""
    t = unicodedata.normalize("NFKC", text or "").translate(APOSTROPHES)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", t).strip()


def text_of(doc):
    """The subject's own words, one normalized line per string: its hashed content, keys and values."""
    return "\n".join(normalize(x) for x in strings(doc))


def text_digest(doc):
    """The sha256 of the screened words, in any order: a hard line follows them to any goal that
    repeats them, however its keys are ordered."""
    words = "\n".join(sorted(set(text_of(doc).split("\n"))))
    return hashlib.sha256(words.encode("utf-8", "surrogatepass")).hexdigest()


def word_key(text):
    """One phrase as a hard line remembers it: normalized, case-folded, hashed."""
    t = normalize(text).casefold() if isinstance(text, str) else ""
    return hashlib.sha256(t.encode("utf-8", "surrogatepass")).hexdigest() if t else None


def goal_gaps(doc):
    """{gap_id: summary} of a goal (M9.11: each gap names its gap_id and a one-line summary)."""
    gaps = doc.get("gaps") if isinstance(doc, dict) and isinstance(doc.get("gaps"), list) else []
    return {str(g.get("gap_id")): g.get("summary") for g in gaps if isinstance(g, dict) and g.get("gap_id")}


def word_keys(kind, doc, only=None):
    """A goal's own words: its statement and each gap's summary (only=gap ids: just those gaps'). A goal
    derived again from the same gaps repeats them whatever its id, measurements or eval, so a hard line
    follows them (a mission has one file)."""
    if kind != "goal" or not isinstance(doc, dict):
        return []
    gaps = goal_gaps(doc)
    xs = [s for g, s in gaps.items() if only is None or g in only]
    if only is None or len(gaps) <= 1:
        xs.append(doc.get("statement"))
    return sorted({k for k in map(word_key, xs) if k})


def hard_words(cwd):
    """Every word key a hard line has fixed (the goal module skips gaps that repeat one)."""
    return {w for e in _events.read(cwd) if e.get("event") == "ethics.blocked"
            for w in ((e.get("data") or {}).get("words") or []) if isinstance(w, str)}


def held_gaps(cwd):
    """Every gap id a hard line has fixed: the gap is held by its id as well as by its words."""
    return {g for e in _events.read(cwd) if e.get("event") == "ethics.blocked"
            for g in ((e.get("data") or {}).get("gaps") or []) if isinstance(g, str)}


def screen(text):
    """[(category, matched phrase)] in CATEGORIES order, one per matched category."""
    out = []
    t = "\n".join(normalize(x) for x in (text or "").split("\n"))
    for c, rxs in _RX.items():
        for rx in rxs:
            m = rx.search(t)
            if m:
                out.append((c, m.group(0)))
                break
    return out


def rank(tier):
    return TIERS.index(tier) if tier in TIERS else -1


# ── subjects ─────────────────────────────────────────────────────────────────────────
def parse_subject(s):
    """'mission' → ("mission", ""); 'gol_…' → ("goal", id); else None."""
    if not isinstance(s, str):
        return None
    if s == "mission":
        return "mission", ""
    if re.match(r"^gol_[0-9a-f]{20}$", s):
        return "goal", s
    return None


def resolve(cwd, kind, sid):
    """(path, doc) of the subject; LookupError when it does not exist or is not itself."""
    path = M.path_of(cwd, kind, sid)
    if os.path.islink(path):
        raise LookupError("%s is a symlink: a subject is its own file (a review or consent never carries over)"
                          % M.rel(cwd, path))
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        raise LookupError("no %s%s (%s)" % (kind, " " + sid if sid else "", M.rel(cwd, path)) if doc is None else
                          "%s is not a JSON object" % M.rel(cwd, path))
    own = doc.get(kind + "_id")
    if not isinstance(own, str) or not M.ID.match(own) or (sid and own != sid):
        raise LookupError("%s: its %s_id %r is not the harness-issued id of this file" % (M.rel(cwd, path), kind, own))
    return path, doc


def binding(cwd, kind, sid):
    """The subject as a review binds it: kind, ref, id and the content's sha256."""
    path, doc = resolve(cwd, kind, sid)
    try:
        sha = M.digest(doc)
    except UnicodeError:
        raise LookupError("%s holds text that is not valid Unicode" % M.rel(cwd, path))
    return {"kind": kind, "ref": M.rel(cwd, path), "id": doc[kind + "_id"], "sha256": sha}, doc


# ── a review's own rules (beyond the schema and the core contract) ───────────────────
def rules(rev, matched, subject=None):
    """Problems of a review against the topics the screen matched in its subject (and, for a goal of
    several gaps, the gaps each hard finding is about: a hard line holds those gaps, not their neighbours)."""
    out = []
    findings = rev.get("findings") if isinstance(rev.get("findings"), list) else []
    findings = [f for f in findings if isinstance(f, dict)]
    for i, f in enumerate(findings):
        r = f.get("reason")
        if isinstance(r, str) and ("\n" in r or len(r) > REASON_MAX):
            out.append("findings[%d].reason must be one line of at most %d characters" % (i, REASON_MAX))
    worst = max([rank(f.get("tier")) for f in findings] + [0])
    if rank(rev.get("tier")) != worst:
        out.append("tier must be %s (its worst finding), not %r" % (TIERS[worst], rev.get("tier")))
    for c, phrase in matched:
        mine = [f for f in findings if f.get("category") == c]
        if not mine:
            out.append("screened topic %s has no finding (the subject says %r): add one with a tier and a "
                       "one-line reason" % (c, phrase))
        elif c in HARD_LINE and max(rank(f.get("tier")) for f in mine) < rank("gray"):
            out.append("%s is a hard-line topic the screen matched (%r): a review cannot clear it; gray at "
                       "least, so the human sees it" % (c, phrase))
    gaps = goal_gaps(subject)
    for i, f in enumerate(findings):
        named = f.get("gaps") if isinstance(f.get("gaps"), list) else []
        for g in named:
            if g not in gaps:
                out.append("findings[%d].gaps: %r is not a gap of this goal (%s)" % (i, g, ", ".join(gaps) or "none"))
        if f.get("tier") == "hard" and len(gaps) > 1 and not named:
            out.append("findings[%d] is hard in a goal of %d gaps: it names the gaps it is about (gaps: [gap_id, …]), "
                       "so the hard line holds those and not the others" % (i, len(gaps)))
    return out


def hard_keys(kind, doc, rev):
    """The word keys a hard review fixes: the gaps its hard findings name (a goal of one gap: that gap
    and the statement)."""
    named = {g for f in rev.get("findings", []) if f.get("tier") == "hard" for g in (f.get("gaps") or [])}
    return word_keys(kind, doc, only=named or None)


def hard_gaps(kind, doc, rev):
    """The gap ids a hard review fixes: the gaps its hard findings name (a goal of one gap: that gap)."""
    if kind != "goal":
        return []
    named = {g for f in rev.get("findings", []) if f.get("tier") == "hard" for g in (f.get("gaps") or [])}
    return sorted(g for g in goal_gaps(doc) if not named or g in named)


def review_problems(cwd, rev, path, matched, subject=None):
    return M.problems(rev, "ethics", path) + (rules(rev, matched, subject) if isinstance(rev, dict) else [])


# ── the verdict ──────────────────────────────────────────────────────────────────────
def is_hard(e):
    return e.get("event") == "ethics.blocked" or (e.get("event") == "ethics.reviewed"
                                                  and (e.get("data") or {}).get("tier") == "hard")


def human_consent(e):
    """An ethics.consent only counts as the human's when `mission ethics consent` wrote it."""
    d = e.get("data") or {}
    return e.get("role") == "human" and str(d.get("by", "")).strip() and str(d.get("reason", "")).strip()


def verdict(cwd, kind, sid):
    """(exit, problems, facts) for the subject's current content: 0 clear or consented gray,
    2 block, 3 needs the human's consent."""
    try:
        subj, doc = binding(cwd, kind, sid)
    except LookupError as e:
        return 2, [str(e)], {"state": "unresolved"}
    ref, sha = subj["ref"], subj["sha256"]
    matched, words, mine = screen(text_of(doc)), text_digest(doc), set(word_keys(kind, doc))
    facts = {"subject": ref, "kind": kind, "id": subj["id"], "sha256": sha, "state": "none",
             "screen": {c: p for c, p in matched}, "tier": None, "review": None, "reviews": [],
             "consent_event": None}
    every, recs, hard, consents = [], {}, [], []
    for e in _events.read(cwd):
        d = e.get("data") or {}
        if not str(e.get("event", "")).startswith("ethics."):
            continue
        # a hard line is sticky: for this content, for this goal whatever it says now (a hard-lined goal
        # is retired, not edited), for these exact words under any goal id, and for any goal that repeats
        # its statement or one of its gaps (derived again with a new id, refs or eval)
        if is_hard(e) and (d.get("subject") == ref and (d.get("sha256") == sha or kind == "goal")
                           or d.get("text_sha256") == words
                           or (kind == "goal" and d.get("kind") == "goal" and mine & set(d.get("words") or []))):
            hard.append(e)
        if d.get("subject") != ref or d.get("sha256") != sha:
            continue
        if e.get("event") == "ethics.reviewed" and d.get("ethics_id"):
            every.append(e)
            recs.pop(d["ethics_id"], None)
            recs[d["ethics_id"]] = e        # re-inserted: dict order is the order last recorded
        elif e.get("event") == "ethics.consent" and human_consent(e):
            consents.append(e)
    if hard:
        h = hard[0].get("data") or {}
        facts.update(tier="hard", review=h.get("ethics_id"), state="hard")
        return 2, ["hard line: review %s found illegal, deceptive or harmful work in %s; no consent, later review "
                   "or edit unlocks it%s" % (h.get("ethics_id"), h.get("subject"),
                                             ": retire this goal" if kind == "goal" else
                                             ", only a human-approved change of the mission does")], facts
    if not recs:
        return 2, ["no ethics review of this content of %s (sha256 %s): the Ethics Reviewer writes one "
                   "(mission ethics new --subject %s, then mission ethics record FILE)"
                   % (ref, sha[:12], sid or "mission")], facts
    bad = []
    for eid, e in recs.items():
        p = M.path_of(cwd, "ethics", eid)
        rev, err = M.load(p)
        if not isinstance(rev, dict):
            bad.append("review %s was removed or is unreadable after it was recorded: restore it (git checkout), "
                       "or change the content and review it again" % eid)
            continue
        try:
            bad += ["review %s: %s" % (eid, x) for x in review_problems(cwd, rev, p, matched, doc)]
        except UnicodeError:
            bad.append("review %s holds text that is not valid Unicode" % eid)
            continue
        if rev.get("sha256") != e["data"].get("review_sha256"):
            bad.append("review %s changed after it was recorded (re-record it: mission ethics record)" % eid)
        elif rev.get("tier") != e["data"].get("tier"):
            bad.append("review %s: the recorded tier %r is not the sealed review's %r"
                       % (eid, e["data"].get("tier"), rev.get("tier")))
        top = max((x["data"].get("tier") for x in every if x["data"]["ethics_id"] == eid), key=rank)
        if rank(e["data"].get("tier")) < rank(top):
            bad.append("review %s was re-recorded below its %s tier: a review never lowers (restore it, or write "
                       "a new review; the tier of this content stays %s)" % (eid, top, top))
    if bad:
        facts["state"] = "broken"   # not fixed by another review: the recorded one must come back
        return 2, sorted(set(bad), key=bad.index), facts
    # a later review never lowers the tier of this content; every current gray review needs its own
    # consent, naming that review and its sealed version
    tier = max((e["data"].get("tier") for e in every), key=rank)
    facts["tier"] = tier
    if tier == "clear":
        facts.update(review=list(recs)[-1], state="ok")
        return 0, [], facts
    gray = [e["data"] for e in recs.values() if e["data"].get("tier") == "gray"]
    facts["reviews"] = [{"ethics_id": d["ethics_id"], "review_sha256": d.get("review_sha256")} for d in gray]
    facts["review"] = gray[-1]["ethics_id"]
    pending, matched_consents = [], []
    for d in gray:
        ok = [c for c in consents if c["data"].get("review") == d["ethics_id"]
              and c["data"].get("review_sha256") == d.get("review_sha256")]
        (matched_consents.append(ok[-1]) if ok else pending.append(d["ethics_id"]))
    if pending:
        return 3, ["gray area in %s (review %s): the human's consent is needed for exactly this content "
                   "(mission ethics consent --subject %s --by NAME --reason TEXT)"
                   % (ref, ", ".join(pending), sid or "mission")], dict(facts, pending=pending, state="pending")
    facts.update(consent_event=matched_consents[-1].get("event_id"), state="ok")
    return 0, [], facts


# ── commands ─────────────────────────────────────────────────────────────────────────
def report(rc, bad):
    for b in bad:
        print("%s[ethics]: %s" % ("ASK" if rc == 3 else "BLOCK", b), file=sys.stderr)


def cmd_new(cwd, kind, sid):
    try:
        subj, doc = binding(cwd, kind, sid)
    except LookupError as e:
        report(2, [str(e)])
        return 2
    matched = screen(text_of(doc))
    eid = M.new_id("ethics")
    rev = {"ethics_id": eid, "subject": subj, "screen": [c for c, _ in matched], "findings": [], "tier": "",
           "status": "recorded", "created_at": M.now()}
    path = M.save(M.path_of(cwd, "ethics", eid), rev)
    print(path)
    if matched:
        print("[ethics] review %s of %s: address each screened topic with a finding (category, tier "
              "clear|gray|hard, one-line reason): %s" % (eid, subj["ref"], "; ".join(
                  "%s (%r)%s" % (c, p, ", gray at least" if c in HARD_LINE else "") for c, p in matched)),
              file=sys.stderr)
    else:
        print("[ethics] review %s of %s: the screen matched nothing; add any finding you see, set the tier, "
              "then mission ethics record %s" % (eid, subj["ref"], M.rel(cwd, path)), file=sys.stderr)
    return 0


def cmd_record(cwd, path):
    rev, err = M.load(path)
    if not isinstance(rev, dict):
        report(2, ["cannot read the review %s: %s" % (path, err or "not a JSON object")])
        return 2
    eid = rev.get("ethics_id", "")
    want = M.path_of(cwd, "ethics", str(eid))
    if os.path.realpath(path) != os.path.realpath(want):
        report(2, ["a review lives at %s (got %s)" % (M.rel(cwd, want), M.rel(cwd, path))])
        return 2
    s = rev.get("subject") if isinstance(rev.get("subject"), dict) else {}
    parsed = parse_subject(s.get("id", "")) if s.get("kind") == "goal" else ("mission", "")
    try:
        cur, doc = binding(cwd, *parsed) if parsed else (None, None)
    except LookupError as e:
        report(2, [str(e)])
        return 2
    if not cur or cur != s:
        report(2, ["the subject changed since this review began (or its binding was edited): start a new "
                   "review (mission ethics new --subject %s)" % (s.get("id") if s.get("kind") == "goal" else "mission")])
        return 2
    rev.pop("sha256", None)
    try:
        bad = review_problems(cwd, rev, path, screen(text_of(doc)), doc)
        if not bad:
            M.seal(rev)
    except UnicodeError:
        bad = ["the review holds text that is not valid Unicode"]
    if bad:
        report(2, bad)
        return 2
    M.save(path, rev)
    kind, sid = parsed
    mine = [e for e in _events.read(cwd) if e.get("event") == "ethics.reviewed"
            and (e.get("data") or {}).get("ethics_id") == eid]
    if not mine or mine[-1]["data"].get("review_sha256") != rev["sha256"]:   # re-recording the same file is a no-op
        refs = {"review": M.rel(cwd, path), "subject": cur["ref"]}
        words = text_digest(doc)
        M.emit(cwd, "ethics.reviewed", role="ethics-reviewer", refs=refs, ethics_id=eid, subject=cur["ref"],
               sha256=cur["sha256"], text_sha256=words, review_sha256=rev["sha256"], tier=rev["tier"], kind=kind)
        if rev["tier"] == "hard":
            cats = [c for c in list(CATEGORIES) + ["other"]
                    if any(f["category"] == c and f["tier"] == "hard" for f in rev["findings"])]
            M.emit(cwd, "ethics.blocked", role="ethics-reviewer", refs=refs, ethics_id=eid, subject=cur["ref"],
                   sha256=cur["sha256"], text_sha256=words, categories=cats, kind=kind, words=hard_keys(kind, doc, rev),
                   gaps=hard_gaps(kind, doc, rev))
    print("[ethics] %s recorded: tier %s for %s" % (eid, rev["tier"], cur["ref"]), file=sys.stderr)
    rc, bad, facts = verdict(cwd, kind, sid)
    if facts.get("state") == "hard" and kind == "goal" and doc.get("status") != "blocked":
        M.save(M.path_of(cwd, kind, sid), dict(doc, status="blocked"))   # retired; status is outside its sha256
    report(rc, bad)
    return rc


def cmd_check(cwd, kind, sid, as_json):
    rc, bad, facts = verdict(cwd, kind, sid)
    if as_json:
        print(json.dumps(dict(facts, exit=rc, problems=bad), indent=1))
    report(rc, bad)
    if rc == 0 and not as_json:
        print("[ethics] %s ok: %s%s" % (facts["subject"], facts["tier"],
                                        " with consent" if facts.get("consent_event") else ""), file=sys.stderr)
    return rc


def cmd_consent(cwd, kind, sid, by, reason):
    by, reason = (by or "").strip(), (reason or "").strip()
    if not by or not reason:
        print("mission ethics consent: --by NAME and --reason TEXT are required (the consent lives in the "
              "goal with its reason)", file=sys.stderr)
        return 64
    rc, bad, facts = verdict(cwd, kind, sid)
    if facts.get("tier") == "hard":
        report(2, ["a hard line cannot be unlocked by consent"] + bad)
        return 2
    if rc == 2:
        report(2, bad)
        return 2
    if facts.get("tier") == "clear":
        report(2, ["nothing to consent to: the review of %s is clear" % facts["subject"]])
        return 2
    if rc == 0:
        print("[ethics] already consented (%s)" % facts["consent_event"], file=sys.stderr)
        return 0
    eids = []
    for r in facts["reviews"]:
        if r["ethics_id"] not in facts["pending"]:
            continue
        try:
            eids.append(M.approve(cwd, "ethics.consent", facts["subject"], facts["sha256"], by, reason,
                                  review=r["ethics_id"], review_sha256=r["review_sha256"], kind=kind,
                                  subject_id=facts["id"]))
        except ValueError as e:
            print("mission ethics consent: %s" % e, file=sys.stderr)
            return 64
    if kind == "goal":   # the consents live inside the goal, one per review; `approval` is outside its sha256
        path, doc = resolve(cwd, kind, sid)
        appr = doc.get("approval") if isinstance(doc.get("approval"), dict) else {}
        old = appr.get("ethics") if isinstance(appr.get("ethics"), dict) else {}
        keep = old.get("consents") if old.get("sha256") == facts["sha256"] and isinstance(old.get("consents"), dict) else {}
        shas = {r["ethics_id"]: r["review_sha256"] for r in facts["reviews"]}
        for rid, ev in zip(facts["pending"], eids):
            keep[rid] = {"event_id": ev, "by": by, "reason": reason, "review_sha256": shas.get(rid), "at": M.now()}
        appr["ethics"] = {"sha256": facts["sha256"], "consents": keep}   # consents to other content are dropped
        doc["approval"] = appr
        M.save(path, doc)
    print("[ethics] consent recorded for %s by %s (review %s)" % (facts["subject"], by, ", ".join(facts["pending"])),
          file=sys.stderr)
    return 0


def parse_args(argv):
    opts, pos, k = {}, [], None
    for a in argv:
        if k:
            opts[k], k = a, None
        elif a == "--json":
            opts["json"] = True
        elif a.startswith("--"):
            k = a[2:].replace("-", "_")
        else:
            pos.append(a)
    if k:
        opts[k] = ""
    return opts, pos


def main(argv, cwd):
    cmd, (o, pos) = argv[0], parse_args(argv[1:])
    if cmd == "record":
        if not pos:
            print("mission ethics record FILE", file=sys.stderr)
            return 64
        return cmd_record(cwd, pos[0])
    if cmd == "screen" and "text" in o:
        print(json.dumps({c: p for c, p in screen(o["text"])}, indent=1))
        return 0
    if cmd not in ("new", "check", "consent", "screen"):
        print("mission ethics: unknown command %r (new, record, check, consent, screen)" % cmd, file=sys.stderr)
        return 64
    subj = parse_subject(o.get("subject", ""))
    if not subj:
        print("mission ethics %s: --subject mission|<goal_id> is required" % cmd, file=sys.stderr)
        return 64
    if cmd == "new":
        return cmd_new(cwd, *subj)
    if cmd == "check":
        return cmd_check(cwd, subj[0], subj[1], o.get("json"))
    if cmd == "consent":
        return cmd_consent(cwd, subj[0], subj[1], o.get("by"), o.get("reason"))
    try:
        _, doc = resolve(cwd, *subj)
    except LookupError as e:
        report(2, [str(e)])
        return 2
    print(json.dumps({c: p for c, p in screen(text_of(doc))}, indent=1))
    return 0
