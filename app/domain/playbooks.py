"""Grounded answer material for every error code the KB covers.

The playbooks are not a replacement for retrieval.  They are the *verified*
distillation of the current KB articles, keyed by the error codes from the
manifest.  Retrieval still decides which pages are in scope and supplies the
citations; the playbook makes the reply deterministic and testable when the
LLM is unavailable or tries to drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Playbook:
    code: str
    doc: str
    topic: str
    category: str
    title: str
    steps: tuple[str, ...]
    priority: str = "P2"
    keywords: tuple[str, ...] = field(default_factory=tuple)
    ticket_by_default: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


CODE_PLAYBOOKS: dict[str, Playbook] = {
    "NID-423": Playbook(
        code="NID-423",
        doc="KB-006",
        topic="lockout",
        category="identity_access",
        title="NakhlaID account locked (NID-423)",
        steps=(
            "Wait **30 minutes**: the lock is released automatically.",
            "Or unlock now at https://id.nakhla.example/unlock: enter your work email and approve the NakhlaKey prompt (or use a backup code). This does not change your password.",
            "If you also forgot the password, reset it at https://id.nakhla.example/reset (KB-004); a password reset unlocks the account too.",
        ),
        priority="P2",
        keywords=("lockout", "locked", "sign-in", "signin"),
        notes=(
            "If it keeps locking, remove the old password from the mail app on your phone (KB-014).",
            "Three or more lockouts in one day without a known cause is a possible password-guessing attack and should go to Security Operations.",
        ),
    ),
    "NX-112": Playbook(
        code="NX-112",
        doc="KB-002",
        topic="vpn",
        category="network_access",
        title="VPN device certificate missing (NX-112)",
        steps=(
            "Open **NakhlaManage > Sync** and wait for it to finish.",
            "Restart the laptop.",
            "Check that NakhlaManage shows **Compliant** (KB-010). A laptop that was offline for more than 30 days must sync once before the VPN accepts it.",
            "Reconnect NakhlaConnect.",
        ),
        priority="P2",
        keywords=("vpn", "certificate", "nakhlamanage", "compliant"),
        ticket_by_default=True,
    ),
    "NX-403": Playbook(
        code="NX-403",
        doc="KB-002",
        topic="vpn",
        category="network_access",
        title="VPN access denied (NX-403)",
        steps=(
            "Approve the NakhlaKey prompt within **60 seconds**.",
            "Check that NakhlaKey is enrolled on this account (KB-007).",
            "New or transferred staff need the **VPN-Users** group: request it at https://access.nakhla.example (KB-011).",
            "A disabled account also shows NX-403; the Service Desk can check the account status.",
        ),
        priority="P2",
        keywords=("vpn", "denied", "access", "nakhlakey"),
        ticket_by_default=True,
    ),
    "NX-503": Playbook(
        code="NX-503",
        doc="KB-002",
        topic="vpn",
        category="network_access",
        title="VPN gateway unavailable (NX-503)",
        steps=(
            "Check the status page at https://status.nakhla.example first.",
            "In NakhlaConnect open **Settings > Gateway** and switch to the other gateway, then reconnect.",
            "If it lasts more than 15 minutes and there is no active incident, escalate to Network Operations.",
        ),
        priority="P2",
        keywords=("vpn", "gateway", "unavailable", "overloaded"),
        ticket_by_default=True,
    ),
    "NX-781": Playbook(
        code="NX-781",
        doc="KB-002",
        topic="vpn",
        category="network_access",
        title="VPN client too old (NX-781)",
        steps=(
            "The minimum supported client is **NakhlaConnect 5.4**; the recommended version is **5.4.2**.",
            "Install **5.4.2** from Software Center (search \"NakhlaConnect\") and restart NakhlaConnect. Installers from file shares or old emails are not supported.",
            "If Software Center fails, follow KB-009 (for example SWC-1603).",
            "Do **not** edit `client.ini` or turn off the posture/version check: SP-002 forbids bypassing device security and such changes are reported.",
        ),
        priority="P2",
        keywords=("vpn", "version", "update", "client"),
        ticket_by_default=True,
    ),
    "NX-905": Playbook(
        code="NX-905",
        doc="KB-002",
        topic="vpn",
        category="network_access",
        title="VPN session expired (NX-905)",
        steps=(
            "This is normal after **12 hours**. Reconnect NakhlaConnect.",
            "Approve the NakhlaKey prompt when it appears.",
        ),
        priority="P3",
        keywords=("vpn", "session", "expired"),
    ),
    "WLAN-17": Playbook(
        code="WLAN-17",
        doc="KB-003",
        topic="wifi",
        category="network_access",
        title="Office Wi-Fi authentication rejected (WLAN-17)",
        steps=(
            "Forget **Nakhla-Corp** and join it again.",
            "Open **NakhlaManage > Sync**, wait 2 minutes, then restart the laptop. This renews the device certificate.",
            "If NakhlaManage shows a compliance problem, fix it first (KB-010).",
            "As a temporary workaround, use a wired dock port or Nakhla-Guest for internet-only work.",
        ),
        priority="P3",
        keywords=("wifi", "wireless", "nakhla-corp", "certificate"),
        ticket_by_default=True,
    ),
    "NID-401": Playbook(
        code="NID-401",
        doc="KB-004",
        topic="password",
        category="identity_access",
        title="NakhlaID sign-in rejected (NID-401)",
        steps=(
            "Check Caps Lock and that you are using your full work email.",
            "After 5 failures the account locks for 30 minutes (NID-423, KB-006).",
            "Forgot the password? Reset it yourself at https://id.nakhla.example/reset.",
        ),
        priority="P2",
        keywords=("password", "signin", "wrong"),
    ),
    "NID-460": Playbook(
        code="NID-460",
        doc="KB-004",
        topic="password",
        category="identity_access",
        title="NakhlaID password expired (NID-460)",
        steps=(
            "Passwords expire every 180 days.",
            "Reset it at https://id.nakhla.example/reset with NakhlaKey or a backup code.",
            "Choose at least 14 characters, do not use the word \"Nakhla\", and do not reuse your last 10 passwords.",
            "Update the password on your phone's mail app too, so it does not lock the account (KB-014).",
        ),
        priority="P2",
        keywords=("password", "expired"),
    ),
    "NID-512": Playbook(
        code="NID-512",
        doc="KB-008",
        topic="sso",
        category="identity_access",
        title="App not assigned (NID-512)",
        steps=(
            "You do not have this app assigned yet. Request access at https://access.nakhla.example (KB-011).",
            "Clearing the browser will not help.",
            "Most access requests finish within 2 business days after manager and data-owner approval.",
        ),
        priority="P4",
        keywords=("app", "sso", "access"),
    ),
    "NID-530": Playbook(
        code="NID-530",
        doc="KB-008",
        topic="sso",
        category="identity_access",
        title="Sign-in time mismatch (NID-530)",
        steps=(
            "Your laptop clock is more than 5 minutes out.",
            "Set date and time to **automatic**, then try the app again.",
        ),
        priority="P3",
        keywords=("sso", "clock", "time"),
    ),
    "MFA-201": Playbook(
        code="MFA-201",
        doc="KB-007",
        topic="mfa",
        category="identity_access",
        title="NakhlaKey push not received (MFA-201)",
        steps=(
            "Open NakhlaKey and pull down to refresh. The prompt expires after **60 seconds**.",
            "Check the phone has internet and that date and time are automatic.",
            "Allow NakhlaKey notifications and exclude it from battery optimisation.",
            "While you fix it, choose **Enter a code instead** and type the 6-digit code from the app.",
        ),
        priority="P2",
        keywords=("mfa", "nakhlakey", "push", "notification"),
    ),
    "MFA-310": Playbook(
        code="MFA-310",
        doc="KB-007",
        topic="mfa",
        category="identity_access",
        title="NakhlaKey device not registered (MFA-310)",
        steps=(
            "**Still have the old phone:** in NakhlaKey open Settings > Transfer and scan the code shown on the new phone.",
            "**Old phone gone, backup codes available:** sign in at https://id.nakhla.example/mfa with a backup code, remove the old device, enrol the new phone.",
            "**No old phone and no backup codes:** the Service Desk opens an identity_access ticket; your manager approves and you verify your identity in person or by video call (SP-001).",
            "**Lost or stolen phone:** report it first as a security incident (SP-002): category security, priority P1.",
        ),
        priority="P2",
        keywords=("mfa", "new phone", "nakhlakey", "backup"),
        ticket_by_default=True,
    ),
    "SWC-0404": Playbook(
        code="SWC-0404",
        doc="KB-009",
        topic="software",
        category="software",
        title="Software not available for this device (SWC-0404)",
        steps=(
            "The app is not in the approved catalogue for your device or department.",
            "Ask your manager whether an approved alternative exists.",
            "Otherwise submit a software request: category software, priority P4, with the app name, version, business reason and manager's name.",
        ),
        priority="P4",
        keywords=("software", "install", "catalogue"),
    ),
    "SWC-1603": Playbook(
        code="SWC-1603",
        doc="KB-009",
        topic="software",
        category="software",
        title="Software Center install failed (SWC-1603)",
        steps=(
            "Restart the laptop: a pending update often blocks installs.",
            "Make sure at least **10 GB** is free.",
            "Try the install again from Software Center.",
        ),
        priority="P3",
        keywords=("software", "install", "failed"),
        ticket_by_default=True,
    ),
    "SWC-2001": Playbook(
        code="SWC-2001",
        doc="KB-009",
        topic="software",
        category="software",
        title="Software licence pool exhausted (SWC-2001)",
        steps=(
            "There is nothing to fix on your side: the licence pool is exhausted.",
            "Open a ticket (category software, priority P3) so Endpoint Engineering can add licences.",
        ),
        priority="P3",
        keywords=("software", "licence", "license", "install"),
        ticket_by_default=True,
    ),
    "MDM-12": Playbook(
        code="MDM-12",
        doc="KB-010",
        topic="hardware",
        category="hardware",
        title="Device encryption is off (MDM-12)",
        steps=(
            "Turn disk encryption back on from **NakhlaManage > Fix**.",
            "Keep the laptop on power until it finishes.",
            "Then run NakhlaManage > Sync and restart.",
        ),
        priority="P2",
        keywords=("encryption", "bitlocker", "filevault", "compliance"),
    ),
    "MDM-31": Playbook(
        code="MDM-31",
        doc="KB-010",
        topic="hardware",
        category="hardware",
        title="Too many enrolled devices (MDM-31)",
        steps=(
            "Each person can enrol at most **3 devices**.",
            "Open NakhlaManage > My devices and remove an old device.",
            "Then enrol the new device again.",
        ),
        priority="P3",
        keywords=("device", "enrol", "enroll", "mdm"),
    ),
    "MDM-44": Playbook(
        code="MDM-44",
        doc="KB-010",
        topic="hardware",
        category="hardware",
        title="Operating system below the minimum (MDM-44)",
        steps=(
            "Install the pending OS update. Minimum: Windows 11 23H2, macOS 14, or Ubuntu 22.04 LTS (Ubuntu for Engineering only).",
            "Run NakhlaManage > Sync and restart.",
        ),
        priority="P2",
        keywords=("os", "update", "compliance", "mdm"),
    ),
    "PRN-07": Playbook(
        code="PRN-07",
        doc="KB-013",
        topic="printing",
        category="hardware",
        title="Badge not linked to NakhlaPrint (PRN-07)",
        steps=(
            "Open https://print.nakhla.example > **My badge**.",
            "Type the **6-character code** shown on the printer screen.",
            "Tap your badge again at the printer.",
        ),
        priority="P3",
        keywords=("printer", "badge", "printing"),
    ),
    "PRN-21": Playbook(
        code="PRN-21",
        doc="KB-013",
        topic="printing",
        category="hardware",
        title="NakhlaPrint driver missing (PRN-21)",
        steps=(
            "Install **NakhlaPrint driver 3.1** from Software Center.",
            "Restart the laptop and print again.",
        ),
        priority="P3",
        keywords=("printer", "driver", "printing"),
    ),
    "PRN-33": Playbook(
        code="PRN-33",
        doc="KB-013",
        topic="printing",
        category="hardware",
        title="Colour print quota used up (PRN-33)",
        steps=(
            "The colour quota is **200 pages per month**.",
            "Print in black and white, or ask your manager to approve more in the print portal.",
        ),
        priority="P3",
        keywords=("printer", "colour", "color", "quota"),
    ),
    "NXM-421": Playbook(
        code="NXM-421",
        doc="KB-014",
        topic="mail",
        category="email_collab",
        title="Mail temporarily delayed (NXM-421)",
        steps=(
            "This means \"temporarily delayed, will retry\". The message is not lost.",
            "Check the status page for a mail incident.",
            "If a message is more than **4 hours** late and there is no incident, open a ticket (category email_collab, priority P3).",
        ),
        priority="P3",
        keywords=("mail", "email", "delayed"),
        ticket_by_default=True,
    ),
    "NXM-552": Playbook(
        code="NXM-552",
        doc="KB-014",
        topic="mail",
        category="email_collab",
        title="Mailbox full (NXM-552)",
        steps=(
            "Every mailbox holds **50 GB**. Empty Deleted Items and remove large attachments.",
            "Use the Online Archive: right-click a folder > Archive, or archive items older than 1 year.",
            "Still full after archiving? Open a ticket (category email_collab, priority P3).",
        ),
        priority="P3",
        keywords=("mail", "mailbox", "full", "storage"),
        ticket_by_default=True,
    ),
}

# Error codes that are clearly business/application codes but unknown to the KB.
# Used to say "no documented fix" instead of stretching an unrelated article.
GENERIC_CODE_RE = re.compile(r"\b[A-Z]{2,5}-\d{2,4}\b")
NON_ERROR_CODE_RE = re.compile(r"^(IT|INC|GW|E)-\d+$|^(GATEWAY|VPN)-\d+$")


def playbook_for(code: str | None) -> Playbook | None:
    return CODE_PLAYBOOKS.get((code or "").upper())


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    category: str
    docs: tuple[str, ...]
    priority: str
    keywords: tuple[str, ...]


TOPICS: dict[str, Topic] = {
    "vpn": Topic("vpn", "VPN / remote access", "network_access", ("KB-002",), "P2",
                 ("vpn", "nakhla connect", "nakhla-connect", "nakhlaconnect", "remote access", "gateway", "nx-")),
    "vdi": Topic("vdi", "Nakhla Workspace VDI", "network_access", ("KB-016", "KB-002"), "P2",
                 ("vdi", "nakhla workspace", "virtual desktop", "remote desktop")),
    "wifi": Topic("wifi", "Office Wi-Fi", "network_access", ("KB-003",), "P3",
                  ("wifi", "wi-fi", "wireless", "nakhla-corp", "nakhla guest", "wlan")),
    "password": Topic("password", "NakhlaID password", "identity_access", ("KB-004",), "P2",
                      ("password", "passwd", "sign in", "sign-in", "login", "log in")),
    "lockout": Topic("lockout", "NakhlaID account lockout", "identity_access", ("KB-006",), "P2",
                     ("locked out", "account locked", "lockout", "nid-423")),
    "mfa": Topic("mfa", "NakhlaKey MFA", "identity_access", ("KB-007",), "P2",
                 ("mfa", "nakhlakey", "authenticator", "two-factor", "2fa", "push prompt")),
    "sso": Topic("sso", "Single sign-on", "identity_access", ("KB-008",), "P3",
                 ("single sign-on", "sso", "nakhlaid error", "nid-512", "nid-530")),
    "access": Topic("access", "Access request", "identity_access", ("KB-011",), "P4",
                    ("access request", "permission", "shared drive", "shared mailbox", "group access", "vpn-users")),
    "software": Topic("software", "Software Center / install", "software", ("KB-009",), "P3",
                      ("software center", "install", "licence", "license", "swc-", "application")),
    "hardware": Topic("hardware", "Laptop / dock / device", "hardware", ("KB-010",), "P3",
                      ("laptop", "dock", "monitor", "usb", "device", "nakhlamanage", "mdm-", "battery", "loaner")),
    "printing": Topic("printing", "Printing / NakhlaPrint", "hardware", ("KB-013",), "P3",
                      ("print", "printer", "printing", "prn-", "followme", "toner", "scan")),
    "mail": Topic("mail", "Nakhla Mail", "email_collab", ("KB-014",), "P3",
                  ("mail", "email", "outlook", "mailbox", "calendar", "nxm-")),
    "onboarding": Topic("onboarding", "Onboarding / offboarding", "onboarding", ("KB-012",), "P3",
                        ("onboarding", "offboarding", "new starter", "new hire", "leaver", "contract end", "first day")),
    "security": Topic("security", "Security incident", "security", ("SP-001", "SP-002"), "P1",
                      ("phishing", "phish", "lost laptop", "stolen", "compromise", "suspicious sign-in", "typed my password")),
    "hr": Topic("hr", "HR data access", "identity_access", ("KB-011", "KB-016"), "P4",
                ("hr-people", "hr people", "nakhlapeople", "hr data", "payroll")),
}


def detect_topic(text: str) -> str | None:
    low = text.lower()
    # Specific, unambiguous phrases first: a "new starter with a laptop" is an
    # onboarding issue, not a hardware issue, and "VDI" outranks generic
    # remote-access wording.
    if re.search(
        r"\b(onboarding|offboarding|new starter|new hire|new colleague|first day|leaver|"
        r"contract(?:or)?\b.{0,25}\b(?:end|expir\w*)|start date)\b",
        low,
    ):
        return "onboarding"
    if re.search(r"\b(vdi|nakhla workspace|virtual desktop)\b", low):
        return "vdi"
    if re.search(r"\b(phone|mobile)\b.{0,25}\b(at home|left at home)\b|\b(left|forgot|forgotten)\b.{0,25}\b(phone|mobile)\b", low):
        return "mfa"
    best: tuple[int, str] | None = None
    for key, topic in TOPICS.items():
        # Longer keywords are more specific; weight them higher.
        score = sum(len(kw) for kw in topic.keywords if kw in low)
        if score and (best is None or score > best[0]):
            best = (score, key)
    if best:
        return best[1]
    # Very small fallback for common phrasings.
    if re.search(r"\b(vpn|connect from home|remote work)\b", low):
        return "vpn"
    if re.search(r"\b(wi-?fi|wlan)\b", low):
        return "wifi"
    if re.search(r"\b(password|sign in|login)\b", low):
        return "password"
    return None


def topic_for_code(code: str | None) -> str | None:
    play = playbook_for(code)
    return play.topic if play else None


def known_generic_code(code: str | None) -> bool:
    if not code:
        return False
    return bool(GENERIC_CODE_RE.fullmatch(code.upper())) and not NON_ERROR_CODE_RE.match(code.upper())


def extract_codes(text: str) -> list[str]:
    found: list[str] = []
    for match in GENERIC_CODE_RE.finditer(text.upper()):
        code = match.group(0)
        if NON_ERROR_CODE_RE.match(code):
            continue
        if code not in found:
            found.append(code)
    return found
