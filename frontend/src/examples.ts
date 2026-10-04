// Example questions, verbatim from the repository's evaluation material. They are inputs only: selecting one runs
// it against the backend like any other question. No expected answer is stored here.

export interface Example {
  label: string;
  source: string; // where the question comes from in the repository
  questions: string[]; // more than one: a conversation, asked in order in the same session
}

export const EXAMPLES: Example[] = [
  {
    label: "Two organizations, one retrieval is not enough",
    source: "evaluation/streaming/cases.py · s6-uc-ru-scope",
    questions: [
      "At UConn, what is the University Travel Card suspension rule, and at Rutgers, what is the travel advance rule?",
    ],
  },
  {
    label: "Two intents",
    source: "evaluation/multi_intent/cases.py · mi-ut-ru",
    questions: [
      "How often must UT Austin user accounts be reviewed, and is there a limit on the gratuities Rutgers will reimburse for business travel?",
    ],
  },
  {
    label: "Two document versions",
    source: "evaluation/multi_intent/cases.py · mi-ctl-compare",
    questions: ["Compare the February 1, 2026 and July 1, 2026 UConn Travel Card penalty rules."],
  },
  {
    label: "Follow-up with a temporal change",
    source: "evaluation/session/cases.py · s-temporal",
    questions: [
      "What did UConn's February 2026 procedures say about when a University Travel Card is suspended?",
      "Does that still apply under the current procedures?",
    ],
  },
  {
    label: "Follow-up chain",
    source: "evaluation/session/cases.py · s-chain",
    questions: [
      "What is UConn's University Travel Card suspension rule?",
      "Does that apply currently?",
      "What happens after 90 days?",
    ],
  },
  {
    label: "Institution outside the corpus",
    source: "evaluation/session/cases.py · s-unsupported",
    questions: ["What are Rutgers' rules for travel advances?", "Does Harvard University have the same rule?"],
  },
];
