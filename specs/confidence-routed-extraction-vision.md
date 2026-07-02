# Vision Brief: Confidence-Routed Document Extraction

**Date:** 2026-06-22
**Author:** Eric Rooney
**Status:** Approved

---

## The Problem

AI-assisted document field mapping produces errors in 2–20% of fields per document — and essentially never comes back 100% accurate on a first draft. Error rates spike further for document types not yet represented in the system's reference data (cold-start problem). Specialist reviewers currently have no way to know *which* fields are systematically unreliable, so every field gets the same level of scrutiny. A reviewer spends 5–10 minutes per document on corrections under normal conditions; significantly more when a draft is badly wrong. With a lean review team, reviewer time is the remaining constraint on throughput.

---

## Who Feels It

Specialist reviewers — domain experts with a couple of years of training — who validate AI-drafted extractions at high volume. Their value is in catching what the AI gets wrong, but without knowing which fields to watch for, they review everything equally whether it needs it or not.

---

## Stakeholders

- **Decision maker:** Operations manager / department head owning the productivity target
- **Reviewers:** Senior reviewers (escalation path for difficult cases)
- **Affected parties:** Management consuming accuracy dashboards; the specialist reviewers whose daily workflow changes

---

## Current State

Reviewers use a side-by-side web interface: source documents on the left, AI-generated draft on the right. They edit inline and mark the document done. Informally, the same few fields tend to be wrong repeatedly — but there's no systematic record of which ones, and no mechanism to flag them for special attention.

---

## Alternatives Considered

- **Manual Claude chat:** Can produce drafts via back-and-forth prompting. Slow, not reproducible, no learning loop, no reliability signal — effectively a one-off tool.
- **RPA with hardcoded validation rules:** Works, but requires knowing which fields to validate upfront, is expensive to build and maintain, breaks when new document types are added, and doesn't improve over time.
- **Why build:** Neither alternative learns from reviewer behavior. The core gap is a feedback loop that turns correction history into routing intelligence — which requires a purpose-built system.

---

## Strategic Context

Productivity improvement is the goal. With AI already handling the bulk of extraction, the next lever is making review faster and more accurate — by directing reviewer attention to the fields that actually need it, rather than asking them to re-examine everything equally.

---

## The Vision

Reviewers treat AI output the way they'd treat a document produced by a competent human colleague — decent by default, with predictable error patterns in known spots. Fewer surprises. When something is wrong, it's flagged. The system is consistent in a way individual human analysts aren't — no good days or bad days, no variance in quality across the team. Over time, it gets better: the more documents go through review, the more precisely the system knows where to direct human attention.

---

## Key Capabilities

**Must have (MVP):**
1. Analyst persona configuration via prompt — the system is told what kind of analyst it is, making it domain-agnostic without code changes
2. Reference document management — view and save reference docs the AI draws on when generating drafts
3. Document ingestion + AI draft generation — submit a portfolio of input documents, receive a structured (JSON) draft output via Claude API
4. Inline review and edit — side-by-side source/draft view, edit fields inline, mark document done

**Must have (core feature — built on MVP):**
5. Edit capture → per-field reliability model — every approved document's diff is recorded; change rates aggregate into a per-field reliability score
6. Confidence-based field highlighting — fields the AI consistently gets wrong are flagged red in future reviews

**Later:**
7. Vector DB integration — connect to historical document pairs for richer extraction context (assumed to exist externally; not required for v1)

---

## Inspiration

- **Anti-inspiration — RPA systems:** Rigid, hardcoded logic that can't adapt to new document types without manual rework. The opposite of what this should be.
- **Anti-inspiration — manual Claude chat:** Demonstrates the approach is possible but not reproducible or scalable.

---

## What Success Looks Like

- **Early signal (1–2 weeks):** Full workflow completes end-to-end — ingest, draft, edit, save. Output quality after editing is acceptable. Reviewer time per document is not significantly worse than the fully manual process.
- **Real outcome (1–3 months):** Reviewer throughput is measurably faster than manual. Reliability model has enough data to flag high-error fields in red. Reviewers report spending less time hunting for errors and more time on genuine judgment calls.

---

## Risks & Assumptions

- **Key assumption:** Field error rates are consistent enough across documents to be learnable — that "date of issue being wrong on passport documents" is a repeatable pattern, not random noise. If errors are genuinely random per document, the reliability model learns nothing.
- **Biggest risk:** Overfitting to specific document types, countries, or languages. The reliability model must degrade gracefully for new input types — defaulting to mandatory review rather than applying a score trained on different data. New combinations must earn their reliability score, not inherit one.

---

## Dependencies

- **Claude API** — required for all AI reasoning and draft generation steps; structured JSON output assumed
- **Vector DB** — assumed to exist externally; system functions without it but extraction quality is lower
- **Web infrastructure** — local deployment for capstone, AWS-compatible for production

---

## Constraints & Context

- **Stack:** Python / FastAPI + Claude API (structured JSON responses); web app
- **Timeline:** Days, not weeks — this is a capstone project
- **Deployment:** Local initially; AWS-compatible architecture
- **Test document types:** Scientific papers, legal documents, handwritten notes
- **Scope:** Generic and domain-agnostic — no credential evaluation rules, no CRM integrations, no proprietary field logic

---

## Open Questions

1. **Reliability model granularity (central):** Per-field is the floor. Useful version is `field × document_type`, possibly `× source_country` or `× language`. Too coarse = flags everything, no signal. Too fine = never enough samples. The grouping key must be configurable — this cannot be hardcoded.
2. **Minimum sample threshold:** How many documents does a field-group need before its reliability score is trusted enough to drive routing? Below threshold, default is mandatory review.
3. **Touch definition edge cases:** Does a deleted field count as touched? A field the AI left blank that the human fills in? Whitespace-only edits? Current lean: deletions and additions count; whitespace does not — but this must be an explicit config, not an assumption.

---

## Future Considerations

- Dashboard for tracking per-field accuracy rates over time (management stakeholder mentioned this explicitly)
- Feedback loop from reliability data back into AI prompting/logic to actively reduce error rates, not just flag them
- Vector DB integration with historical document pairs
- Scalability for production-volume deployment (explicitly out of scope for this version)

---

## Feature Breakdown

**Sequencing strategy: Riskiest first** — Feature 1b (document ingestion + AI draft generation) is built first because it tests whether Claude API can reliably extract and structure fields from scientific papers, legal docs, and handwritten notes. If extraction quality is insufficient, everything downstream is moot.

### Epic 1: Document Processing Pipeline

- [ ] **Feature 1a:** Pipeline configuration — analyst persona prompt setup and reference document management
- [ ] **Feature 1b:** Document ingestion + AI draft generation — submit source doc portfolio, receive structured JSON draft via Claude API ← *start here*
- [ ] **Feature 1c:** Review and approval — side-by-side editing, inline field corrections, mark done

### Epic 2: Reliability Learning Loop

- [ ] **Feature 2a:** Edit capture — record per-field diffs between draft and approved final
- [ ] **Feature 2b:** Reliability model — aggregate touch rates into per-field scores with configurable grouping key
- [ ] **Feature 2c:** Confidence highlighting — flag high-error-rate fields red during review

**Recommended starting feature:** Feature 1b (Document ingestion + AI draft generation) — tests the core AI extraction assumption before investing in the full review interface.
