export type Scenario = {
  title: string;
  hint: string;
  email: string | null;
  message: string;
};

export const SCENARIOS: Scenario[] = [
  {
    title: "Account lockout",
    hint: "Grounded answer, no ticket",
    email: "sara.mikhail@nakhla.example",
    message: "NakhlaID says NID-423 when I try to sign in. What should I do?",
  },
  {
    title: "Vague VPN report",
    hint: "Needs clarification",
    email: null,
    message: "My VPN is not working. Can you fix it?",
  },
  {
    title: "Outdated VPN guide",
    hint: "Policy conflict",
    email: "tariq.hamdan@nakhla.example",
    message:
      "NakhlaConnect shows NX-781 on my laptop. I have version 5.1 and the VPN guide says 4.8 or later is supported, so what is wrong?",
  },
  {
    title: "Undocumented error",
    hint: "Escalation offer",
    email: "omar.farah@nakhla.example",
    message: "The NakhlaCRM mobile app shows error CRM-1150 every time it syncs my contacts.",
  },
  {
    title: "VPN error, then a ticket",
    hint: "Full approval flow",
    email: "sara.mikhail@nakhla.example",
    message: "My NakhlaConnect VPN shows NX-112 since this morning. I did all the usual steps and I still cannot work. Please open a ticket.",
  },
  {
    title: "Software Center failure",
    hint: "Ticket draft, then reject",
    email: "karim.saba@nakhla.example",
    message: "Software Center fails with SWC-2001 when I install my licensed app. Please open a ticket for it.",
  },
  {
    title: "Known gateway incident",
    hint: "Status page, no duplicate",
    email: "omar.farah@nakhla.example",
    message: "NakhlaConnect keeps failing with NX-503 since this morning. Please open a ticket.",
  },
  {
    title: "Finance VPN exception",
    hint: "Department routing",
    email: "fadi.mansour@nakhla.example",
    message: "I'm in Finance. NakhlaConnect shows NX-403 and I cannot connect. If it needs a ticket, prepare it for me.",
  },
];
