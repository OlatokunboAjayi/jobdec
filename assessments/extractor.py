"""
Assessment Link Ingestion & Multi-Platform Parser (Features 6, 7, 8, R2).
Extracts and normalizes assessment URLs from:
1. Incoming emails / MIME HTML / plaintext payloads and cached `inbox.json`
2. Application workflow tracker (`Job Applications/applications.csv` in `OA/Assessment` stage)
3. Direct URLs or tracking wrappers (Microsoft SafeLinks, Google redirects, SendGrid/Mailgun)
Supports 12+ assessment platforms plus local mock portal URLs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import pathlib
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple, Union

from assessments.contracts import AssessmentPlatform, AssessmentTask
import candidate as CAND


class _AnchorHrefParser(HTMLParser):
    """Lightweight stdlib HTML anchor href extractor."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            for k, v in attrs:
                if k.lower() == "href" and v:
                    self.hrefs.append(v.strip())

# Platform regex patterns ordered by specificity
PLATFORM_URL_PATTERNS: List[Tuple[AssessmentPlatform, re.Pattern[str]]] = [
    (
        AssessmentPlatform.HACKERRANK,
        re.compile(
            r"https?://(?:www\.)?(?:hackerrank\.com/(?:tests?|work/tests|challenges)/[a-zA-Z0-9_\-]+|hr\.gs/[a-zA-Z0-9_\-]+)",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.CODILITY,
        re.compile(
            r"https?://(?:app\.)?codility\.com/(?:c/run|test|programmers)/[a-zA-Z0-9_\-]+/?",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.CODESIGNAL,
        re.compile(
            r"https?://(?:app\.)?codesignal\.com/(?:invite|public-test|evaluation|test)/[a-zA-Z0-9_\-]+/?",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.TESTGORILLA,
        re.compile(
            r"https?://(?:app\.)?testgorilla\.com/(?:test|c|s)/[a-zA-Z0-9_\-]+/?",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.CRITERIA_CCAT,
        re.compile(
            r"https?://(?:www\.)?ondemandassessment\.com/(?:link/index|verify/index|test)/[a-zA-Z0-9_\-]+/?",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.HIREVUE,
        re.compile(
            r"https?://[a-zA-Z0-9\-]+\.hirevue\.com/(?:interview|app|interviews)/[a-zA-Z0-9_\-]+/?",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.PYMETRICS,
        re.compile(
            r"https?://(?:www\.|app\.)?pymetrics\.com/(?:candidate|game|assessments)/[a-zA-Z0-9_\-]+/?",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.SHL_TALENTCENTRAL,
        re.compile(
            r"https?://(?:talentcentral\.[a-z0-9\-]+\.shl\.com|[a-z0-9\-]+\.shl\.com)/(?:player|assessment|candidate)/[a-zA-Z0-9_\-/?&=]+",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.GOOGLE_FORMS,
        re.compile(
            r"https?://(?:docs\.google\.com/forms/d/e/[a-zA-Z0-9_\-]+/(?:viewform|formResponse)|forms\.gle/[a-zA-Z0-9_\-]+)",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.TYPEFORM,
        re.compile(
            r"https?://[a-zA-Z0-9\-]+\.typeform\.com/to/[a-zA-Z0-9_\-]+",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.MICROSOFT_FORMS,
        re.compile(
            r"https?://(?:forms\.office\.com|forms\.cloud\.microsoft)/r/[a-zA-Z0-9_\-]+",
            re.IGNORECASE,
        ),
    ),
    (
        AssessmentPlatform.WORKDAY,
        re.compile(
            r"https?://[a-zA-Z0-9\-]+\.wd[0-9]\.myworkdayjobs\.com/[^\"'\s<>]*(?:assessment|candidateExperience|task)[^\"'\s<>]*",
            re.IGNORECASE,
        ),
    ),
]

MOCK_PORTAL_PATTERN = re.compile(
    r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?/portal/([a-zA-Z0-9_\-]+)[^\"'\s<>]*",
    re.IGNORECASE,
)

GENERIC_URL_PATTERN = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


def unwrap_redirect_url(url: str) -> str:
    """
    Unwraps common email tracking redirect wrappers (Microsoft SafeLinks, Google URL redirects,
    SendGrid, Mailgun, etc.) to recover the canonical target URL.
    """
    if not url:
        return ""
    cleaned = url.strip().rstrip(".,;)>\"'")

    # Loop up to 3 levels of nested redirect query parameters
    for _ in range(3):
        parsed = urllib.parse.urlparse(cleaned)
        host = (parsed.netloc or "").lower()
        query_params = urllib.parse.parse_qs(parsed.query)

        # Microsoft Outlook SafeLinks: ?url=<encoded>
        if "safelinks.protection.outlook.com" in host and "url" in query_params:
            cleaned = urllib.parse.unquote(query_params["url"][0])
            continue

        # Google Redirect: google.com/url?q=<encoded> or ?url=<encoded>
        if "google.com" in host and parsed.path == "/url":
            target = query_params.get("q") or query_params.get("url")
            if target:
                cleaned = urllib.parse.unquote(target[0])
                continue

        # Generic redirect trackers with ?url= or ?target= or ?redirect= or ?u=
        if any(tracker in host for tracker in ("sendgrid.net", "mailgun.net", "mandrillapp.com", "hubspotlinks.com", "awstrack.me", "click.")):
            for param_key in ("url", "target", "redirect", "dest", "u"):
                if param_key in query_params and query_params[param_key][0].startswith("http"):
                    cleaned = urllib.parse.unquote(query_params[param_key][0])
                    break
            else:
                break
            continue

        break

    return cleaned


def classify_platform(url: str, context_text: str = "") -> AssessmentPlatform:
    """Classifies a URL and optional email/workflow context into an AssessmentPlatform."""
    canonical = unwrap_redirect_url(url)

    # Check local mock portal pattern first
    mock_match = MOCK_PORTAL_PATTERN.search(canonical)
    if mock_match:
        slug = mock_match.group(1).lower()
        mapping = {
            "hackerrank": AssessmentPlatform.HACKERRANK,
            "codility": AssessmentPlatform.CODILITY,
            "codesignal": AssessmentPlatform.CODESIGNAL,
            "testgorilla": AssessmentPlatform.TESTGORILLA,
            "ccat": AssessmentPlatform.CRITERIA_CCAT,
            "criteria_ccat": AssessmentPlatform.CRITERIA_CCAT,
            "hirevue": AssessmentPlatform.HIREVUE,
            "pymetrics": AssessmentPlatform.PYMETRICS,
            "shl": AssessmentPlatform.SHL_TALENTCENTRAL,
            "shl_talentcentral": AssessmentPlatform.SHL_TALENTCENTRAL,
            "google_forms": AssessmentPlatform.GOOGLE_FORMS,
            "typeform": AssessmentPlatform.TYPEFORM,
            "microsoft_forms": AssessmentPlatform.MICROSOFT_FORMS,
            "workday": AssessmentPlatform.WORKDAY,
        }
        return mapping.get(slug, AssessmentPlatform.CUSTOM)

    for platform, pattern in PLATFORM_URL_PATTERNS:
        if pattern.search(canonical):
            return platform

    # Inspect context cues if URL is a generic portal or synthesized link
    combined = f"{canonical} {context_text}".lower()
    if "hackerrank" in combined or "hr.gs" in combined:
        return AssessmentPlatform.HACKERRANK
    if "codility" in combined:
        return AssessmentPlatform.CODILITY
    if "codesignal" in combined:
        return AssessmentPlatform.CODESIGNAL
    if "testgorilla" in combined:
        return AssessmentPlatform.TESTGORILLA
    if "ccat" in combined or "criteria" in combined or "ondemandassessment" in combined:
        return AssessmentPlatform.CRITERIA_CCAT
    if "hirevue" in combined:
        return AssessmentPlatform.HIREVUE
    if "pymetrics" in combined:
        return AssessmentPlatform.PYMETRICS
    if "shl.com" in combined or "talentcentral" in combined or "shl " in combined:
        return AssessmentPlatform.SHL_TALENTCENTRAL
    if "forms.gle" in combined or "docs.google.com/forms" in combined or "google form" in combined:
        return AssessmentPlatform.GOOGLE_FORMS
    if "typeform.com" in combined:
        return AssessmentPlatform.TYPEFORM
    if "forms.office.com" in combined or "forms.cloud.microsoft" in combined:
        return AssessmentPlatform.MICROSOFT_FORMS
    if "myworkdayjobs.com" in combined or "workday" in combined:
        return AssessmentPlatform.WORKDAY

    return AssessmentPlatform.CUSTOM


def extract_urls_from_text_or_html(content: str) -> List[str]:
    """Extracts and deduplicates all candidate assessment URLs from HTML or plaintext."""
    if not content:
        return []

    raw_urls: List[str] = []

    # 1. Extract href attributes from HTML anchor tags if HTML tags exist
    if "<a" in content.lower() or "<html" in content.lower():
        try:
            parser = _AnchorHrefParser()
            parser.feed(content)
            for href in parser.hrefs:
                if href.startswith(("http://", "https://")):
                    raw_urls.append(href)
        except Exception:
            pass

    # 2. Extract plaintext URLs
    for match in GENERIC_URL_PATTERN.findall(content):
        raw_urls.append(match.rstrip(".,;)>\"'"))

    # Unwrap redirects and deduplicate while preserving order
    seen = set()
    result: List[str] = []
    for u in raw_urls:
        unwrapped = unwrap_redirect_url(u)
        # Skip unsubscribe / privacy / static image links
        lower_u = unwrapped.lower()
        if any(skip in lower_u for skip in ("unsubscribe", "privacy-policy", "mailto:", ".png", ".jpg", ".gif", ".css")):
            continue
        if unwrapped and unwrapped not in seen:
            seen.add(unwrapped)
            result.append(unwrapped)

    # Sort recognized assessment platform URLs first
    result.sort(key=lambda u: 0 if classify_platform(u) != AssessmentPlatform.CUSTOM else 1)
    return result


def _make_task_id(company: str, platform: str, url: str) -> str:
    digest = hashlib.sha1(f"{company}:{platform}:{url}".encode("utf-8")).hexdigest()[:8]
    clean_co = re.sub(r"[^a-z0-9]+", "-", (company or "unknown").lower()).strip("-") or "task"
    return f"assess-{clean_co}-{digest}"


def ingest_from_applications_csv(csv_path: Optional[pathlib.Path] = None) -> List[AssessmentTask]:
    """
    Extracts active assessment tasks from `Job Applications/applications.csv` where
    Stage matches `OA/Assessment` (or contains assessment links in Next Action / Notes).
    """
    target_csv = csv_path or CAND.APPLICATIONS
    if not target_csv.exists():
        return []

    tasks: List[AssessmentTask] = []
    candidate_email = os.getenv("CANDIDATE_EMAIL") or CAND.fact("EMAIL") or os.getenv("GMAIL_EMAIL", "") or "candidate@example.com"

    try:
        with open(target_csv, mode="r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stage = (row.get("Stage") or "").strip()
                if not re.search(r"oa|assessment", stage, re.IGNORECASE):
                    continue

                company = (row.get("Company") or "Unknown").strip()
                role = (row.get("Role") or "Software Engineer").strip()
                job_url = (row.get("Job URL") or "").strip()
                next_action = (row.get("Next Action") or "").strip()
                notes = (row.get("Notes") or "").strip()
                deadline = (row.get("Deadline") or row.get("Next Action Date") or "").strip() or None

                context_blob = f"{next_action} {notes} {job_url}"
                extracted_urls = extract_urls_from_text_or_html(f"{next_action} {notes}")
                raw_url = extracted_urls[0] if extracted_urls else job_url
                canonical_url = unwrap_redirect_url(raw_url) if raw_url else f"https://portal.assessment.local/{company.lower()}"
                platform = classify_platform(canonical_url, context_text=context_blob)

                task_id = _make_task_id(company, platform.value, canonical_url)
                tasks.append(
                    AssessmentTask(
                        task_id=task_id,
                        company=company,
                        role=role,
                        platform=platform,
                        raw_url=raw_url or canonical_url,
                        canonical_url=canonical_url,
                        deadline_iso=deadline,
                        candidate_email=candidate_email,
                        metadata={
                            "source": "applications_csv",
                            "row_id": row.get("ID", ""),
                            "stage": stage,
                            "notes": notes,
                            "next_action": next_action,
                        },
                    )
                )
    except Exception:
        pass

    return tasks


def ingest_from_inbox_cache(inbox_path: Optional[pathlib.Path] = None) -> List[AssessmentTask]:
    """
    Extracts assessment tasks from the triaged email cache (`Job Applications/autofill/agents/cache/inbox.json`).
    """
    target_json = inbox_path or (CAND.DATA / "agents" / "cache" / "inbox.json")
    if not target_json.exists():
        target_json = CAND.AUTOFILL / "agents" / "cache" / "inbox.json"
    if not target_json.exists():
        return []

    tasks: List[AssessmentTask] = []
    candidate_email = os.getenv("CANDIDATE_EMAIL") or CAND.fact("EMAIL") or os.getenv("GMAIL_EMAIL", "") or "candidate@example.com"

    try:
        data = json.loads(target_json.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return []

        for uid, entry in data.items():
            if not isinstance(entry, dict):
                continue

            company = (entry.get("company") or entry.get("sender") or "Unknown").strip()
            role = (entry.get("role") or "Software Engineer Intern").strip()
            subject = (entry.get("subject") or "").strip()
            summary = (entry.get("summary") or "").strip()
            action_needed = (entry.get("action_needed") or "").strip()
            body = (entry.get("body") or entry.get("html") or "").strip()
            deadline = (entry.get("deadline") or "").strip() or None

            blob = f"{subject} {summary} {action_needed} {body}"
            blob_l = blob.lower()
            is_assessment = entry.get("kind") == "assessment" or any(
                kw in blob_l
                for kw in (
                    "codility",
                    "hackerrank",
                    "codesignal",
                    "hirevue",
                    "testgorilla",
                    "pymetrics",
                    "pre-interview assessment",
                    "online assessment",
                    "complete the assessment",
                )
            )
            if not is_assessment:
                continue

            # Normalize raw sender email into clean company name when summary has "Company | ..."
            if "@" in company and "|" in summary:
                prefix = summary.split("|", 1)[0].strip()
                if prefix and len(prefix) < 40:
                    company = prefix
            urls = extract_urls_from_text_or_html(blob)
            raw_url = urls[0] if urls else entry.get("url", "")
            platform = classify_platform(raw_url, context_text=blob)
            canonical_url = unwrap_redirect_url(raw_url) if raw_url else f"https://{platform.value}.assessment.local/{uid}"

            task_id = _make_task_id(company, platform.value, canonical_url)
            tasks.append(
                AssessmentTask(
                    task_id=task_id,
                    company=company,
                    role=role,
                    platform=platform,
                    raw_url=raw_url or canonical_url,
                    canonical_url=canonical_url,
                    deadline_iso=deadline,
                    source_email_uid=str(uid),
                    candidate_email=candidate_email,
                    metadata={
                        "source": "inbox_json",
                        "subject": subject,
                        "summary": summary,
                        "action_needed": action_needed,
                    },
                )
            )
    except Exception:
        pass

    return tasks


def extract_assessment_tasks(
    source: Union[str, pathlib.Path, Dict[str, Any], List[Any]] = "auto"
) -> List[AssessmentTask]:
    """
    Primary ingestion contract (PROJECT.md R2):
    `extractor.extract_assessment_tasks(source: str) -> List[AssessmentTask]`

    Supports:
    - `"auto"` or `"all"`: Combines `applications.csv` and `inbox.json` assessment tasks.
    - `"applications"` or path to `.csv`: Extracts from `applications.csv`.
    - `"inbox"` or path to `.json`: Extracts from `inbox.json`.
    - Direct URL string (`http://...` or `https://...`): Wraps URL into an `AssessmentTask`.
    - Raw email HTML / text string containing URLs: Extracts URLs and creates `AssessmentTask` items.
    - Dict or list of dicts representing email payloads or task specifications.
    """
    candidate_email = os.getenv("CANDIDATE_EMAIL", "candidate@example.com")

    if isinstance(source, list):
        out: List[AssessmentTask] = []
        for item in source:
            out.extend(extract_assessment_tasks(item))
        return out

    if isinstance(source, dict):
        if "canonical_url" in source and "task_id" in source:
            return [AssessmentTask.model_validate(source)]
        company = str(source.get("company") or source.get("sender") or "Unknown")
        role = str(source.get("role") or "Software Engineer")
        body = f"{source.get('subject', '')} {source.get('body', '')} {source.get('html', '')} {source.get('url', '')}"
        urls = extract_urls_from_text_or_html(body)
        raw_url = urls[0] if urls else str(source.get("url") or "")
        canonical = unwrap_redirect_url(raw_url) if raw_url else f"https://custom.assessment.local/{company.lower()}"
        platform = classify_platform(canonical, context_text=body)
        return [
            AssessmentTask(
                task_id=_make_task_id(company, platform.value, canonical),
                company=company,
                role=role,
                platform=platform,
                raw_url=raw_url or canonical,
                canonical_url=canonical,
                deadline_iso=source.get("deadline"),
                source_email_uid=str(source.get("uid", "")) or None,
                candidate_email=candidate_email,
                metadata={"source": "dict_payload"},
            )
        ]

    if isinstance(source, pathlib.Path):
        if source.suffix.lower() == ".csv":
            return ingest_from_applications_csv(source)
        if source.suffix.lower() == ".json":
            return ingest_from_inbox_cache(source)
        if source.exists():
            return extract_assessment_tasks(source.read_text(encoding="utf-8", errors="replace"))
        return []

    src_str = str(source or "").strip()
    if not src_str:
        return []

    if src_str.lower() in ("auto", "all"):
        csv_tasks = ingest_from_applications_csv()
        inbox_tasks = ingest_from_inbox_cache()
        seen_ids = set()
        combined: List[AssessmentTask] = []
        for t in csv_tasks + inbox_tasks:
            if t.task_id not in seen_ids:
                seen_ids.add(t.task_id)
                combined.append(t)
        return combined

    if src_str.lower() in ("applications", "csv"):
        return ingest_from_applications_csv()

    if src_str.lower() in ("inbox", "email_cache"):
        return ingest_from_inbox_cache()

    # Check if src_str is a file path on disk
    if len(src_str) < 260 and not src_str.startswith(("http://", "https://")) and "\n" not in src_str:
        p = pathlib.Path(src_str)
        if p.exists():
            return extract_assessment_tasks(p)

    # Check if src_str is a single URL or text/HTML containing URLs
    urls = extract_urls_from_text_or_html(src_str)
    if urls:
        tasks: List[AssessmentTask] = []
        for u in urls:
            canonical = unwrap_redirect_url(u)
            platform = classify_platform(canonical, context_text=src_str)
            tasks.append(
                AssessmentTask(
                    task_id=_make_task_id("Direct", platform.value, canonical),
                    company="Direct Assessment",
                    role="Software Engineer",
                    platform=platform,
                    raw_url=u,
                    canonical_url=canonical,
                    candidate_email=candidate_email,
                    metadata={"source": "text_or_url"},
                )
            )
        return tasks

    return []
