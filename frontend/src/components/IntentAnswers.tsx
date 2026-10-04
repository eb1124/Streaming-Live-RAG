// The answer of a multi-intent question, intent by intent. Each intent shows the verified claims the backend
// attributes to it, with their citations, or says plainly that it has no grounded answer. The text is the stored
// claims', unchanged; an intent without claims is never given one.

import { ChevronRight } from "lucide-react";
import { claimSentence, type AnswerByIntent, type AnswerByOrganization, type ClaimRef, type IntentAnswer, type Run } from "../lib/model";
import { useStore } from "../state/store";
import { StatusBadge } from "./ui";

function ClaimText({ run, claim }: { run: Run; claim: ClaimRef }) {
  const { panel, openPanel } = useStore();
  const citations = run.turn?.answer?.citations ?? [];
  const active = panel?.kind === "source" && panel.runId === run.id ? panel.citation : null;
  return (
    <span>
      {claimSentence(claim.claim)}
      {(claim.claim.citations ?? []).map((n) => {
        const citation = citations.find((c) => c.number === n);
        if (!citation) return <span key={n}>[{n}]</span>;
        return (
          <button
            key={n}
            className={`marker ${active === n ? "on" : ""}`}
            onClick={() => openPanel({ kind: "source", runId: run.id, chunkId: citation.chunk_id, citation: n })}
            aria-label={`Open the source of citation ${n}`}
            title={citation.document ?? citation.doc_id}
          >
            {n}
          </button>
        );
      })}{" "}
    </span>
  );
}

/** Why an intent has no grounded answer, from its stored record: its status, and whether it names an organization
 *  outside the corpus. */
function unanswered(intent: IntentAnswer["intent"]): string {
  if (intent.outside_corpus?.length) return `This part names an organization outside the corpus (${intent.outside_corpus.join(", ")}).`;
  if (intent.status === "unsupported" || intent.admissible === false) return "No relevant evidence was retrieved for this intent.";
  return "The retrieved evidence does not answer this intent: no verified claim of the answer is attributed to it.";
}

function Intent({ run, item }: { run: Run; item: IntentAnswer }) {
  const { openPanel } = useStore();
  const { intent, claims, gaps } = item;
  const answered = claims.length > 0;
  return (
    <section className={`intent-answer ${answered ? "" : "none"}`}>
      <button className="intent-answer-head" onClick={() => openPanel({ kind: "intent", runId: run.id, index: intent.index })} title="Inspect this intent">
        <span className="eyebrow">Intent {intent.index + 1}</span>
        {intent.organizations.length ? <span className="org">{intent.organizations.join(", ")}</span> : null}
        <StatusBadge tone={answered ? "ok" : "warn"}>{answered ? "Answered" : "Insufficient evidence"}</StatusBadge>
        <ChevronRight aria-hidden />
      </button>
      <p className="intent-q">{intent.text}</p>
      {answered ? (
        <div className="answer-text">
          {claims.map((claim) => (
            <ClaimText key={claim.number} run={run} claim={claim} />
          ))}
        </div>
      ) : (
        <p className="intent-none">
          <strong>No grounded answer.</strong> {unanswered(intent)}
        </p>
      )}
      {gaps.map((gap, i) => (
        <p key={i} className="intent-gap">
          <span className="k">Not in the sources</span> {gap}
        </p>
      ))}
    </section>
  );
}

function Gaps({ gaps }: { gaps: string[] }) {
  if (!gaps.length) return null;
  return (
    <div className="intent-gap whole">
      <span className="k">The answer states that the sources do not cover</span>
      <ul>
        {gaps.map((gap, i) => (
          <li key={i}>{gap}</li>
        ))}
      </ul>
    </div>
  );
}

/** The answer of a question the backend kept as one intent, grouped by the organization of the cited sources. */
export function OrganizationAnswers({ run, answer }: { run: Run; answer: AnswerByOrganization }) {
  return (
    <div className="intent-answers">
      <p className="faint small">
        The backend answered this question as one intent. Its verified claims are grouped here by the organization of the sources each one cites.
      </p>
      {answer.organizations.map(({ organization, claims }) => {
        const answered = claims.length > 0;
        return (
          <section key={organization} className={`intent-answer ${answered ? "" : "none"}`}>
            <div className="intent-answer-head static">
              <span className="org">{organization}</span>
              <StatusBadge tone={answered ? "ok" : "warn"}>{answered ? "Answered" : "Insufficient evidence"}</StatusBadge>
            </div>
            {answered ? (
              <div className="answer-text">
                {claims.map((claim) => (
                  <ClaimText key={claim.number} run={run} claim={claim} />
                ))}
              </div>
            ) : (
              <p className="intent-none">
                <strong>No grounded answer.</strong> No verified claim of the answer cites a source from {organization}.
              </p>
            )}
          </section>
        );
      })}
      {answer.other.length ? (
        <section className="intent-answer">
          <div className="intent-answer-head static">
            <span className="eyebrow">Sources without an organization</span>
          </div>
          <div className="answer-text">
            {answer.other.map((claim) => (
              <ClaimText key={claim.number} run={run} claim={claim} />
            ))}
          </div>
        </section>
      ) : null}
      <Gaps gaps={answer.gaps} />
    </div>
  );
}

export function IntentAnswers({ run, answer }: { run: Run; answer: AnswerByIntent }) {
  return (
    <div className="intent-answers">
      {answer.intents.map((item) => (
        <Intent key={item.intent.index} run={run} item={item} />
      ))}
      {answer.unattributed.length ? (
        <section className="intent-answer">
          <div className="intent-answer-head static">
            <span className="eyebrow">Not attributed to an intent</span>
          </div>
          <div className="answer-text">
            {answer.unattributed.map((claim) => (
              <ClaimText key={claim.number} run={run} claim={claim} />
            ))}
          </div>
        </section>
      ) : null}
      <Gaps gaps={answer.gaps} />
    </div>
  );
}
