// Sample requesters from the kit directory (services/data/employees.json).
// The agent looks the employee up itself over MCP; this list only drives the picker.
export type Employee = {
  email: string;
  name: string;
  title: string;
  department: string;
  location: string;
  vpnClient: string | null;
  vip: boolean;
  contractor: boolean;
};

export const EMPLOYEES: Employee[] = [
  { email: "samir.nassar@nakhla.example", name: "Samir Nassar", title: "Chief Executive Officer", department: "Executive Office", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: true, contractor: false },
  { email: "dalia.awwad@nakhla.example", name: "Dalia Awwad", title: "Chief Operating Officer", department: "Executive Office", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: true, contractor: false },
  { email: "hiba.qasem@nakhla.example", name: "Hiba Qasem", title: "Executive Assistant to the CEO", department: "Executive Office", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: true, contractor: false },
  { email: "yousef.darwish@nakhla.example", name: "Yousef Darwish", title: "Engineering Manager", department: "Engineering", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "tariq.hamdan@nakhla.example", name: "Tariq Hamdan", title: "Senior Backend Engineer", department: "Engineering", location: "Remote", vpnClient: "5.1.0", vip: false, contractor: false },
  { email: "noor.barakat@nakhla.example", name: "Noor Barakat", title: "Frontend Engineer", department: "Engineering", location: "Ramallah office", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "karim.saba@nakhla.example", name: "Karim Saba", title: "DevOps Engineer", department: "Engineering", location: "Remote", vpnClient: "5.4.1", vip: false, contractor: false },
  { email: "rami.odeh@nakhla.example", name: "Rami Odeh", title: "QA Engineer", department: "Engineering", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "salma.aziz@nakhla.example", name: "Salma Aziz", title: "Data Engineer", department: "Engineering", location: "Ramallah office", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "rana.khalil@nakhla.example", name: "Rana Khalil", title: "Finance Manager", department: "Finance", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "fadi.mansour@nakhla.example", name: "Fadi Mansour", title: "Accountant", department: "Finance", location: "Ramallah office", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "lama.jaber@nakhla.example", name: "Lama Jaber", title: "Payroll Specialist", department: "Finance", location: "Remote", vpnClient: "5.4.0", vip: false, contractor: false },
  { email: "mona.shaheen@nakhla.example", name: "Mona Shaheen", title: "HR Director", department: "HR", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "ahmad.yassin@nakhla.example", name: "Ahmad Yassin", title: "HR Business Partner", department: "HR", location: "Ramallah office", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "reem.halabi@nakhla.example", name: "Reem Halabi", title: "Recruiter", department: "HR", location: "Remote", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "maya.saleh@nakhla.example", name: "Maya Saleh", title: "Sales Director", department: "Sales", location: "Jerusalem HQ", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "omar.farah@nakhla.example", name: "Omar Farah", title: "Account Executive", department: "Sales", location: "Remote", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "nadia.issa@nakhla.example", name: "Nadia Issa", title: "Account Executive", department: "Sales", location: "Ramallah office", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "bashar.tannous@nakhla.example", name: "Bashar Tannous", title: "Sales Engineer", department: "Sales", location: "Remote", vpnClient: "5.2.3", vip: false, contractor: false },
  { email: "layla.sabbagh@nakhla.example", name: "Layla Sabbagh", title: "Support Team Lead", department: "Support", location: "Ramallah office", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "jad.khatib@nakhla.example", name: "Jad Khatib", title: "Support Specialist", department: "Support", location: "Ramallah office", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "sara.mikhail@nakhla.example", name: "Sara Mikhail", title: "Support Specialist", department: "Support", location: "Remote", vpnClient: "5.4.2", vip: false, contractor: false },
  { email: "ziad.hourani@nakhla.example", name: "Ziad Hourani", title: "Contract QA Tester", department: "Contractors", location: "Remote", vpnClient: null, vip: false, contractor: true },
  { email: "elena.markovic@nakhla.example", name: "Elena Markovic", title: "Contract UX Designer", department: "Contractors", location: "Remote", vpnClient: null, vip: false, contractor: true },
  { email: "basel.amro@nakhla.example", name: "Basel Amro", title: "Contract Financial Analyst", department: "Contractors", location: "Remote", vpnClient: null, vip: false, contractor: true },
];

export const byEmail = (email: string) => EMPLOYEES.find((e) => e.email === email);
