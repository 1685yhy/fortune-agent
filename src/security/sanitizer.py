"""Input sanitization module for Fortune Agent.

Provides comprehensive input cleaning and attack detection:
- Strip HTML/JS/XSS attempts
- Detect and block SQL injection patterns
- Enforce maximum input length
- Block common attack patterns and prompt injection
"""
import re
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


# Regex patterns for dangerous content
HTML_TAG_PATTERN = re.compile(r"<[^>]*>")
SCRIPT_PATTERN = re.compile(
    r"(<script[^>]*>.*?</script>|<script[^>]*/>)", re.IGNORECASE | re.DOTALL
)
JAVASCRIPT_PROTOCOL_PATTERN = re.compile(
    r"javascript\s*:", re.IGNORECASE
)
ON_EVENT_PATTERN = re.compile(
    r"\bon\w+\s*=", re.IGNORECASE
)

# SQL injection patterns
SQL_INJECTION_PATTERNS = [
    re.compile(r"(\bSELECT\b.*\bFROM\b)", re.IGNORECASE),
    re.compile(r"(\bDROP\b\s+\bTABLE\b)", re.IGNORECASE),
    re.compile(r"(\bDELETE\b.*\bFROM\b)", re.IGNORECASE),
    re.compile(r"(\bINSERT\b.*\bINTO\b)", re.IGNORECASE),
    re.compile(r"(\bUPDATE\b.*\bSET\b)", re.IGNORECASE),
    re.compile(r"(\bALTER\b.*\bTABLE\b)", re.IGNORECASE),
    re.compile(r"(\bCREATE\b.*\bTABLE\b)", re.IGNORECASE),
    re.compile(r"(\bEXEC\b|\bEXECUTE\b)", re.IGNORECASE),
    re.compile(r"(\bUNION\b.*\bSELECT\b)", re.IGNORECASE),
    re.compile(r"(\bLOAD_FILE\s*\()", re.IGNORECASE),
    re.compile(r"(\bINTO\s+OUTFILE\b)", re.IGNORECASE),
    re.compile(r"(\bINTO\s+DUMPFILE\b)", re.IGNORECASE),
    re.compile(r"(\bSLEEP\s*\()", re.IGNORECASE),
    re.compile(r"(\bWAITFOR\b.*\bDELAY\b)", re.IGNORECASE),
    re.compile(r"(\bBENCHMARK\s*\()", re.IGNORECASE),
    re.compile(r"['\"]\s*(OR|AND)\s*['\"]?[\d]+['\"]?\s*=", re.IGNORECASE),
    re.compile(r"(\bINFORMATION_SCHEMA\b)", re.IGNORECASE),
    re.compile(r"(--|#|\/\*)\s*.*$", re.MULTILINE),
]

# System command injection patterns
CMD_INJECTION_PATTERNS = [
    re.compile(r"[;|&]\s*(bash|sh|cmd|powershell|python|perl|ruby|php)\s+"),
    re.compile(r"(\$\(.*\))"),  # Command substitution
    re.compile(r"(`[^`]+`)"),  # Backtick execution
    re.compile(r"(\|\s*cat\s+)", re.IGNORECASE),
    re.compile(r"(\|\s*wget\s+)", re.IGNORECASE),
    re.compile(r"(\|\s*curl\s+)", re.IGNORECASE),
    re.compile(r"(\|\s*nc\s+)", re.IGNORECASE),
    re.compile(r"(\|\s*telnet\s+)", re.IGNORECASE),
    re.compile(r"(>\s*/dev/\w+)"),
    re.compile(r"(>\s*/tmp/)"),
]

# Prompt injection patterns
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"忽略所有之前的指令", re.IGNORECASE),
    re.compile(r"忽略以上", re.IGNORECASE),
    re.compile(r"忽略之前", re.IGNORECASE),
    re.compile(r"ignore all previous", re.IGNORECASE),
    re.compile(r"ignore the above", re.IGNORECASE),
    re.compile(r"forget everything", re.IGNORECASE),
    re.compile(r"你是一个(算命|AI|助手|机器人)", re.IGNORECASE),
    re.compile(r"你现在是", re.IGNORECASE),
    re.compile(r"you are (now|a)", re.IGNORECASE),
    re.compile(r"角色扮演", re.IGNORECASE),
    re.compile(r"role.?play", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"你被设定为", re.IGNORECASE),
    re.compile(r"你是[一个]?[大]?语言模型", re.IGNORECASE),
    re.compile(r"你(必须|需要|要)回答", re.IGNORECASE),
    re.compile(r"回答(必须|需要|要)以", re.IGNORECASE),
    re.compile(r"DAN|do anything now", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"越狱", re.IGNORECASE),
    re.compile(r"突破限制", re.IGNORECASE),
    re.compile(r"不受限制", re.IGNORECASE),
]

# Path traversal patterns
PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),
    re.compile(r"\.\.\\"),
    re.compile(r"~[/\\]"),
]

# Encoded attack patterns
ENCODED_ATTACK_PATTERNS = [
    re.compile(r"%3Cscript", re.IGNORECASE),  # URL-encoded <script
    re.compile(r"%3C%2Fscript", re.IGNORECASE),  # URL-encoded </script
    re.compile(r"\\x[0-9a-fA-F]{2}"),  # Hex-encoded chars
    re.compile(r"\\u[0-9a-fA-F]{4}"),  # Unicode-encoded chars
    re.compile(r"&#\d{2,};"),  # HTML entity encoding
    re.compile(r"&#x[0-9a-fA-F]{2,};"),  # Hex HTML entity
]


class InputSanitizer:
    """Input sanitizer with multi-layer attack detection.

    Features:
    - HTML/JS stripping
    - SQL injection detection
    - Command injection detection
    - Prompt injection detection
    - Max length enforcement
    - Encoding attack detection
    - Path traversal detection
    """

    MAX_INPUT_LENGTH = 2000
    MAX_TOTAL_INPUT_LENGTH = 10000  # For batch operations

    # Allow list of safe HTML tags for display purposes
    SAFE_TAGS = {"b", "i", "em", "strong", "p", "br", "span"}

    def __init__(self):
        self.stats = {
            "total_checked": 0,
            "xss_blocked": 0,
            "sql_blocked": 0,
            "cmd_blocked": 0,
            "prompt_injection_blocked": 0,
            "path_traversal_blocked": 0,
            "length_blocked": 0,
        }

    def sanitize(self, text: str, max_length: Optional[int] = None) -> str:
        """Sanitize user input. Strips dangerous content and enforces length."""
        if not text:
            return ""

        self.stats["total_checked"] += 1

        # Enforce max length
        max_len = max_length or self.MAX_INPUT_LENGTH
        if len(text) > max_len:
            text = text[:max_len]
            self.stats["length_blocked"] += 1

        # Strip HTML tags (safer to remove entirely for LLM input)
        text = HTML_TAG_PATTERN.sub("", text)

        # Strip script tags and event handlers
        text = SCRIPT_PATTERN.sub("", text)

        # Strip javascript: protocol
        text = JAVASCRIPT_PROTOCOL_PATTERN.sub("", text)

        # Strip onEvent= handlers
        text = ON_EVENT_PATTERN.sub("", text)

        # Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def detect_attack(self, text: str) -> Tuple[bool, Optional[str]]:
        """Detect if input contains attack patterns.

        Returns:
            Tuple of (is_attack_detected, attack_type)
        """
        if not text:
            return False, None

        # Check SQL injection
        for pattern in SQL_INJECTION_PATTERNS:
            if pattern.search(text):
                self.stats["sql_blocked"] += 1
                logger.warning("SQL injection detected in input: %s...", text[:80])
                return True, "sql_injection"

        # Check command injection
        for pattern in CMD_INJECTION_PATTERNS:
            if pattern.search(text):
                self.stats["cmd_blocked"] += 1
                logger.warning("Command injection detected in input: %s...", text[:80])
                return True, "command_injection"

        # Check XSS (after sanitization, check for remaining patterns)
        remaining_xss = HTML_TAG_PATTERN.search(text)
        if remaining_xss:
            self.stats["xss_blocked"] += 1
            logger.warning("XSS attempt detected in input: %s...", text[:80])
            return True, "xss"

        # Check prompt injection
        for pattern in PROMPT_INJECTION_PATTERNS:
            if pattern.search(text):
                self.stats["prompt_injection_blocked"] += 1
                logger.warning("Prompt injection detected in input: %s...", text[:80])
                return True, "prompt_injection"

        # Check path traversal
        for pattern in PATH_TRAVERSAL_PATTERNS:
            if pattern.search(text):
                self.stats["path_traversal_blocked"] += 1
                logger.warning("Path traversal detected in input: %s...", text[:80])
                return True, "path_traversal"

        # Check encoded attacks
        for pattern in ENCODED_ATTACK_PATTERNS:
            if pattern.search(text):
                logger.warning("Encoded attack detected in input: %s...", text[:80])
                return True, "encoded_attack"

        return False, None

    def clean_and_check(self, text: str, max_length: Optional[int] = None) -> Tuple[str, bool, Optional[str]]:
        """Clean input and check for attacks in one call.

        Returns:
            Tuple of (cleaned_text, is_attack, attack_type)
        """
        cleaned = self.sanitize(text, max_length)
        is_attack, attack_type = self.detect_attack(cleaned)
        return cleaned, is_attack, attack_type

    def get_stats(self) -> dict:
        """Get sanitizer statistics."""
        return dict(self.stats)

    def is_safe_message(self, text: str, max_length: Optional[int] = None) -> bool:
        """Quick check: is this message safe to process?"""
        cleaned, is_attack, _ = self.clean_and_check(text, max_length)
        return not is_attack and len(cleaned) > 0
