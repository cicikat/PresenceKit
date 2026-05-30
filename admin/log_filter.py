"""
Logging filter: redact sensitive query-param values from uvicorn access logs.

uvicorn access records carry args = (client_addr, method, full_path,
http_version, status_code).  full_path (index 2) may contain raw query
strings such as ?token=<secret>.  This filter replaces the values of
sensitive params with *** before the AccessFormatter formats the record.
"""

import logging
import re

_SENSITIVE = re.compile(r'(?i)((?:token|secret)=)[^&\s#]*')


class QuerySanitizeFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            lst = list(record.args)
            lst[2] = _SENSITIVE.sub(r'\1***', str(lst[2]))
            record.args = tuple(lst)
        return True


def install_access_log_sanitizer() -> None:
    logging.getLogger("uvicorn.access").addFilter(QuerySanitizeFilter())
