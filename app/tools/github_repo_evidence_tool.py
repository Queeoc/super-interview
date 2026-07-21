"""GitHub 仓库代码片段检索工具。"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.tools.github_repo_context_tool import (
    GitHubRepoBindingResult,
    invoke_github_mcp_tool,
    resolve_github_repo_binding,
)

_SOURCE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".kt",
    ".scala",
}
_CONFIG_EXTENSIONS = {
    ".yml",
    ".yaml",
    ".toml",
}
_CONFIG_FILENAMES = {
    "dockerfile",
    "pyproject.toml",
    "requirements.txt",
    "pom.xml",
}
_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".kt": "kotlin",
    ".scala": "scala",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
}
_SYMBOL_PATTERNS = (
    re.compile(r"^\s*async\s+def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\("
    ),
    re.compile(
        r"^\s*(?:public|private|protected)?\s*(?:async\s+)?(?:static\s+)?"
        r"[A-Za-z_<>\[\], ?]+\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
    ),
    re.compile(r"^\s*func\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:type|struct|interface|trait|impl)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"),
)
_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_#+./-]{1,}")
_CAMEL_CASE_PATTERN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")
_SEARCH_CODE_LIMIT_PER_KEYWORD = 6
_MAX_FETCHED_PATHS = 18
_MAX_KEYWORDS = 4
_MAX_FALLBACK_SEARCH_TERMS_PER_KEYWORD = 3
_MIN_SNIPPET_LINES = 15
_TARGET_SNIPPET_LINES = 24
_MAX_SNIPPET_LINES = 40
_MAX_SYMBOL_LOOKBACK = 24
_FALLBACK_LINE_PADDING = 8
_BUSINESS_ACTION_TERMS = (
    "ack",
    "await",
    "call",
    "consume",
    "create",
    "delete",
    "emit",
    "execute",
    "handle",
    "invoke",
    "load",
    "process",
    "publish",
    "read",
    "retry",
    "return",
    "run",
    "save",
    "send",
    "start",
    "stream",
    "submit",
    "update",
    "validate",
    "write",
)
_CONTROL_FLOW_TERMS = (
    "catch",
    "except",
    "finally",
    "for ",
    "if ",
    "match ",
    "return",
    "switch",
    "throw",
    "try",
    "while ",
)
_FILE_ROLE_RANK = {
    "implementation_source": 0,
    "supporting_source": 1,
    "config": 2,
    "ignore": 3,
}
_SUPPORTING_SOURCE_NAME_TERMS = (
    "config",
    "configuration",
    "properties",
    "resolver",
    "client",
    "util",
    "utils",
    "helper",
    "factory",
)
_SUPPORTING_SOURCE_PATH_SEGMENTS = {
    "config",
    "configuration",
    "properties",
    "client",
    "adapter",
    "starter",
}
_GENERIC_FALLBACK_WORDS = {
    "service",
    "manager",
    "handler",
    "controller",
    "provider",
    "client",
    "config",
    "configuration",
    "utils",
    "helper",
}


class GitHubRepoEvidenceToolInput(BaseModel):
    """GitHub 仓库代码片段检索输入。"""

    model_config = ConfigDict(extra="forbid")

    resume_markdown: str = Field(default="", description="简历 Markdown 内容")
    resume_metadata: dict[str, Any] = Field(default_factory=dict, description="简历元数据")
    category_key: str = Field(default="GENERAL", description="问题分类")
    question_text: str = Field(default="", description="当前问题")
    answer_text: str = Field(default="", description="候选人最近回答")
    search_keywords: list[str] = Field(default_factory=list, description="LLM 显式提供的代码检索关键词")
    keywords: list[str] = Field(default_factory=list, description="兼容保留：等价于 search_keywords")
    focus_topics: list[str] = Field(default_factory=list, description="兼容保留：追问重点主题")
    missing_signals: list[str] = Field(default_factory=list, description="兼容保留：当前缺失信号")
    top_k: int = Field(default=2, ge=1, description="返回代码片段条数，运行时会收敛到 1..2")


class GitHubRepoEvidenceItem(BaseModel):
    """单条 GitHub 代码证据卡。"""

    model_config = ConfigDict(extra="forbid")

    evidence_type: str = Field(default="code_snippet", description="证据类型，固定为 code_snippet")
    source_path: str = Field(..., description="来源文件路径")
    heading: str = Field(default="", description="最近的函数、类或符号名")
    raw_excerpt: str = Field(..., min_length=1, description="原始代码片段")
    normalized_snippet: str = Field(..., min_length=1, description="规整后的片段摘要")
    matched_terms: list[str] = Field(default_factory=list, description="命中的关键技术词")
    relevance_score: float = Field(default=0.0, description="相关分")
    why_it_matched: str = Field(default="", description="命中原因")
    language: str | None = Field(default=None, description="代码语言")
    start_line: int | None = Field(default=None, description="片段起始行号")
    end_line: int | None = Field(default=None, description="片段结束行号")


class GitHubRepoEvidenceToolResult(BaseModel):
    """GitHub 仓库代码片段检索输出。"""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(default=False, description="是否找到可用代码片段")
    repo_url: str = Field(default="", description="匹配的仓库链接")
    repo_name: str = Field(default="", description="owner/repo")
    matched_project_title: str = Field(default="", description="绑定到的简历项目标题")
    matched_project_excerpt: str = Field(default="", description="绑定到的简历项目片段")
    matched_files: list[str] = Field(default_factory=list, description="命中的仓库文件")
    items: list[GitHubRepoEvidenceItem] = Field(default_factory=list, description="代码证据卡列表")
    retrieval_reason: str = Field(default="", description="检索结果说明")


class _KeywordQuery(BaseModel):
    """单个关键技术词的搜索与匹配配置。"""

    model_config = ConfigDict(extra="forbid")

    display_term: str
    search_term: str
    match_terms: list[str]
    fallback_search_terms: list[str] = Field(default_factory=list)


class _SearchCodeMatch(BaseModel):
    """search_code 返回的轻量结果。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    result_order: int = 0


class _SearchHit(BaseModel):
    """单个关键词对某个文件的命中。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    display_term: str
    search_term: str
    match_terms: list[str]
    query_order: int
    result_order: int
    file_role: str
    is_source_file: bool


class _SnippetCandidate(BaseModel):
    """候选代码片段。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    heading: str = ""
    language: str | None = None
    start_line: int
    end_line: int
    raw_excerpt: str
    normalized_snippet: str
    matched_terms: list[str]
    exact_match_count: int
    keyword_coverage: int
    file_role: str
    is_source_file: bool
    symbol_match: bool
    search_rank: int
    relevance_score: float
    why_it_matched: str


async def github_repo_evidence_tool(**kwargs: Any) -> GitHubRepoEvidenceToolResult:
    """根据关键技术词从 GitHub 仓库中检索可出题的代码片段。"""

    tool_input = GitHubRepoEvidenceToolInput.model_validate(kwargs)
    normalized_top_k = max(1, min(2, int(tool_input.top_k or 2)))
    keyword_queries = _build_keyword_queries(tool_input)
    logger.info(
        "github code evidence retrieval started question_preview={}, keywords={}, top_k={}",
        tool_input.question_text[:80],
        [item.display_term for item in keyword_queries],
        normalized_top_k,
    )
    if not tool_input.resume_markdown.strip():
        return GitHubRepoEvidenceToolResult(found=False, retrieval_reason="resume_missing")
    if not keyword_queries:
        return GitHubRepoEvidenceToolResult(found=False, retrieval_reason="github_code_keywords_missing")

    binding = resolve_github_repo_binding(
        resume_markdown=tool_input.resume_markdown,
        question_text=tool_input.question_text,
        answer_text=tool_input.answer_text,
        focus_topics=tool_input.focus_topics,
        missing_signals=tool_input.missing_signals,
    )
    if not binding.found:
        return GitHubRepoEvidenceToolResult(
            found=False,
            repo_url=binding.repo_url,
            repo_name=binding.repo_name,
            matched_project_title=binding.matched_project_title,
            matched_project_excerpt=binding.matched_project_excerpt,
            retrieval_reason=binding.retrieval_reason or "github_context_missing",
        )

    try:
        search_hits = await _search_repo_code(
            binding=binding,
            keyword_queries=keyword_queries,
            use_fallback_terms=False,
        )
        used_fallback_search = False
        if not search_hits:
            search_hits = await _search_repo_code(
                binding=binding,
                keyword_queries=keyword_queries,
                use_fallback_terms=True,
            )
            used_fallback_search = bool(search_hits)
        snippet_candidates = await _build_snippet_candidates(
            binding=binding,
            search_hits=search_hits,
            keyword_queries=keyword_queries,
        )
    except Exception as exc:
        logger.warning(
            "github code evidence retrieval unavailable repo_name={}, error={}",
            binding.repo_name,
            exc,
        )
        return GitHubRepoEvidenceToolResult(
            found=False,
            repo_url=binding.repo_url,
            repo_name=binding.repo_name,
            matched_project_title=binding.matched_project_title,
            matched_project_excerpt=binding.matched_project_excerpt,
            retrieval_reason="github_code_search_unavailable",
        )

    items = _select_top_items(snippet_candidates, top_k=normalized_top_k)
    matched_files = _build_matched_files(items)
    if not items:
        fallback_item, fallback_reason = await _build_non_code_fallback_item(
            binding=binding,
            keyword_queries=keyword_queries,
        )
        if fallback_item is not None:
            logger.info(
                "github evidence retrieval completed found=true repo_name={} fallback_reason={} source_path={}",
                binding.repo_name,
                fallback_reason,
                fallback_item.source_path,
            )
            return GitHubRepoEvidenceToolResult(
                found=True,
                repo_url=binding.repo_url,
                repo_name=binding.repo_name,
                matched_project_title=binding.matched_project_title,
                matched_project_excerpt=binding.matched_project_excerpt,
                matched_files=_build_matched_files([fallback_item]),
                items=[fallback_item],
                retrieval_reason=fallback_reason,
            )
        logger.info(
            "github code evidence retrieval completed found=false repo_name={} reason=github_code_search_empty",
            binding.repo_name,
        )
        return GitHubRepoEvidenceToolResult(
            found=False,
            repo_url=binding.repo_url,
            repo_name=binding.repo_name,
            matched_project_title=binding.matched_project_title,
            matched_project_excerpt=binding.matched_project_excerpt,
            matched_files=matched_files,
            retrieval_reason="github_code_search_empty",
        )

    logger.info(
        "github code evidence retrieval completed found=true repo_name={} matched_files={} top_items={}",
        binding.repo_name,
        matched_files,
        [
            {
                "path": item.source_path,
                "lines": [item.start_line, item.end_line],
                "terms": item.matched_terms,
                "score": item.relevance_score,
            }
            for item in items
        ],
    )
    return GitHubRepoEvidenceToolResult(
        found=True,
        repo_url=binding.repo_url,
        repo_name=binding.repo_name,
        matched_project_title=binding.matched_project_title,
        matched_project_excerpt=binding.matched_project_excerpt,
        matched_files=matched_files,
        items=items,
        retrieval_reason="github_code_search_fallback_matched" if used_fallback_search else "github_code_search_matched",
    )


def _build_keyword_queries(tool_input: GitHubRepoEvidenceToolInput) -> list[_KeywordQuery]:
    """构建用于代码搜索的关键技术词配置。"""

    raw_keywords = list(tool_input.search_keywords or tool_input.keywords)

    result: list[_KeywordQuery] = []
    seen_display_terms: set[str] = set()
    for raw_keyword in raw_keywords:
        display_term = raw_keyword.strip()
        if not display_term:
            continue
        lowered_display_term = display_term.lower()
        if lowered_display_term in seen_display_terms:
            continue
        seen_display_terms.add(lowered_display_term)
        match_terms = _build_match_terms(display_term)
        search_term = _pick_search_term(display_term, match_terms)
        fallback_search_terms = _build_fallback_search_terms(display_term)
        result.append(
            _KeywordQuery(
                display_term=display_term,
                search_term=search_term,
                match_terms=match_terms,
                fallback_search_terms=fallback_search_terms,
            )
        )
        if len(result) >= _MAX_KEYWORDS:
            break
    return result


async def _search_repo_code(
    *,
    binding: GitHubRepoBindingResult,
    keyword_queries: list[_KeywordQuery],
    use_fallback_terms: bool,
) -> list[_SearchHit]:
    """逐个关键技术词调用 search_code，并按文件类型做两阶段筛选。"""

    hits_by_role: dict[str, list[_SearchHit]] = {
        "implementation_source": [],
        "supporting_source": [],
        "config": [],
    }
    seen_hits: set[tuple[str, str]] = set()
    seen_queries: set[str] = set()
    for query_order, keyword_query in enumerate(keyword_queries):
        search_terms = (
            keyword_query.fallback_search_terms[:_MAX_FALLBACK_SEARCH_TERMS_PER_KEYWORD]
            if use_fallback_terms
            else [keyword_query.search_term]
        )
        for fallback_order, search_term in enumerate(search_terms):
            if not search_term.strip():
                continue
            query = _build_search_query(binding.repo_name, search_term)
            lowered_query = query.lower()
            if lowered_query in seen_queries:
                continue
            seen_queries.add(lowered_query)
            payload = await invoke_github_mcp_tool("search_code", {"query": query})
            matches = _parse_search_code_matches(payload)
            logger.debug(
                "github code search keyword={} search_term={} repo_name={} fallback={} match_count={}",
                keyword_query.display_term,
                search_term,
                binding.repo_name,
                use_fallback_terms,
                len(matches),
            )
            for match in matches[:_SEARCH_CODE_LIMIT_PER_KEYWORD]:
                file_role = _classify_file_kind(match.path)
                if file_role == "ignore":
                    continue
                hit_key = (match.path.lower(), keyword_query.display_term.lower())
                if hit_key in seen_hits:
                    continue
                seen_hits.add(hit_key)
                hit = _SearchHit(
                    path=match.path,
                    display_term=keyword_query.display_term,
                    search_term=search_term,
                    match_terms=_unique_list([*keyword_query.match_terms, search_term]),
                    query_order=query_order,
                    result_order=fallback_order * _SEARCH_CODE_LIMIT_PER_KEYWORD + match.result_order,
                    file_role=file_role,
                    is_source_file=file_role in {"implementation_source", "supporting_source"},
                )
                hits_by_role[file_role].append(hit)

    selected_hits = (
        hits_by_role["implementation_source"]
        or hits_by_role["supporting_source"]
        or hits_by_role["config"]
    )
    return selected_hits[: _MAX_FETCHED_PATHS]


async def _build_snippet_candidates(
    *,
    binding: GitHubRepoBindingResult,
    search_hits: list[_SearchHit],
    keyword_queries: list[_KeywordQuery],
) -> list[_SnippetCandidate]:
    """抓取命中文件并为每个文件构建最适合出题的代码片段。"""

    if not search_hits:
        return []

    grouped_hits: dict[str, list[_SearchHit]] = {}
    for hit in search_hits:
        grouped_hits.setdefault(hit.path, []).append(hit)

    ordered_paths = sorted(
        grouped_hits,
        key=lambda path: (
            min(_FILE_ROLE_RANK.get(item.file_role, 9) for item in grouped_hits[path]),
            min(item.query_order for item in grouped_hits[path]),
            min(item.result_order for item in grouped_hits[path]),
            path,
        ),
    )[:_MAX_FETCHED_PATHS]

    candidates: list[_SnippetCandidate] = []
    for path in ordered_paths:
        grouped = grouped_hits[path]
        content = await _load_repo_file_content(binding=binding, path=path)
        if not content.strip():
            continue
        candidate = _build_snippet_candidate(
            path=path,
            content=content,
            grouped_hits=grouped,
            keyword_queries=keyword_queries,
        )
        if candidate is None:
            continue
        candidates.append(candidate)

    return sorted(
        candidates,
        key=lambda item: (
            -item.exact_match_count,
            -item.keyword_coverage,
            _FILE_ROLE_RANK.get(item.file_role, 9),
            0 if item.symbol_match else 1,
            item.search_rank,
            item.path,
        ),
    )


async def _load_repo_file_content(*, binding: GitHubRepoBindingResult, path: str) -> str:
    """读取命中文件内容。"""

    payload = await invoke_github_mcp_tool(
        "get_file_contents",
        {
            "owner": binding.owner,
            "repo": binding.repo,
            "path": path,
        },
    )
    return _extract_primary_text(payload)


async def _build_non_code_fallback_item(
    *,
    binding: GitHubRepoBindingResult,
    keyword_queries: list[_KeywordQuery],
) -> tuple[GitHubRepoEvidenceItem | None, str]:
    """代码搜索为空时，退到 README 或简历绑定项目片段。"""

    readme_item = await _build_readme_fallback_item(binding=binding, keyword_queries=keyword_queries)
    if readme_item is not None:
        return readme_item, "github_readme_fallback_matched"

    project_excerpt = binding.matched_project_excerpt.strip()
    if project_excerpt:
        return (
            GitHubRepoEvidenceItem(
                evidence_type="project_binding_excerpt",
                source_path="resume_project_binding",
                heading=binding.matched_project_title or "matched_project",
                raw_excerpt=project_excerpt,
                normalized_snippet=_truncate_text(project_excerpt, 260),
                matched_terms=[item.display_term for item in keyword_queries],
                relevance_score=0.1,
                why_it_matched="代码与 README 未命中，使用简历绑定项目片段作为追问兜底证据",
                language=None,
                start_line=None,
                end_line=None,
            ),
            "github_project_binding_fallback",
        )
    return None, "github_code_search_empty"


async def _build_readme_fallback_item(
    *,
    binding: GitHubRepoBindingResult,
    keyword_queries: list[_KeywordQuery],
) -> GitHubRepoEvidenceItem | None:
    """从 README 中提取与关键词最相关的段落。"""

    readme_content = ""
    readme_path = "README.md"
    for path in ("README.md", "readme.md"):
        try:
            readme_content = await _load_repo_file_content(binding=binding, path=path)
        except Exception as exc:
            logger.debug("github README fallback unavailable path={} error={}", path, exc)
            continue
        if readme_content.strip():
            readme_path = path
            break
    if not readme_content.strip():
        return None

    section = _select_readme_fallback_section(readme_content, keyword_queries)
    if section is None:
        return None
    heading, excerpt, matched_terms, matched_search_terms = section
    return GitHubRepoEvidenceItem(
        evidence_type="repo_readme_excerpt",
        source_path=readme_path,
        heading=heading,
        raw_excerpt=excerpt,
        normalized_snippet=_truncate_text(excerpt, 260),
        matched_terms=matched_terms,
        relevance_score=0.2 + len(matched_terms) * 0.1,
        why_it_matched=_build_readme_why_it_matched(
            matched_terms=matched_terms,
            matched_search_terms=matched_search_terms,
        ),
        language="markdown",
        start_line=None,
        end_line=None,
    )


def _build_snippet_candidate(
    *,
    path: str,
    content: str,
    grouped_hits: list[_SearchHit],
    keyword_queries: list[_KeywordQuery],
) -> _SnippetCandidate | None:
    """从单个文件内容中截出最有价值的代码窗口。"""

    lines = content.splitlines()
    if not lines:
        return None

    keyword_map = _merge_keyword_hits(grouped_hits, keyword_queries)
    line_hits = _find_line_hits(lines, keyword_map)
    if not line_hits:
        return None

    best_line = _select_best_line_hit(lines, line_hits, keyword_map, path=path)
    match_line, matched_display_terms = best_line
    start_line, end_line, heading, symbol_match = _build_snippet_window(lines, match_line)
    if not symbol_match:
        start_line = _trim_leading_non_body_lines(lines, start_line, end_line)
        heading = _fallback_heading_from_path(path)
    replacement_window = _find_nearby_business_window(lines, start_line, end_line, heading, path=path)
    if replacement_window is not None:
        start_line, end_line, heading, symbol_match = replacement_window
    raw_excerpt = "\n".join(lines[start_line - 1 : end_line]).rstrip()
    if not raw_excerpt:
        return None

    excerpt_terms = _collect_excerpt_terms(raw_excerpt, keyword_map)
    file_hit_terms = _unique_list([hit.display_term for hit in grouped_hits])
    if not excerpt_terms:
        excerpt_terms = matched_display_terms
    excerpt_terms = _unique_list([*file_hit_terms, *excerpt_terms])
    exact_match_count = _count_exact_matches(raw_excerpt, excerpt_terms, keyword_map)
    keyword_coverage = len(excerpt_terms)
    language = _detect_language(path)
    file_role = _classify_file_kind(path)
    is_source_file = file_role in {"implementation_source", "supporting_source"}
    search_rank = min(hit.result_order for hit in grouped_hits)
    role_score = {
        "implementation_source": 0.45,
        "supporting_source": 0.2,
        "config": 0.05,
    }.get(file_role, 0.0)
    relevance_score = round(
        float(exact_match_count)
        + keyword_coverage * 0.6
        + role_score
        + (0.2 if symbol_match else 0.0),
        3,
    )
    why_it_matched = _build_why_it_matched(
        path=path,
        heading=heading,
        matched_terms=excerpt_terms,
        start_line=start_line,
        end_line=end_line,
        fallback_search_terms=_collect_used_fallback_search_terms(grouped_hits),
    )
    return _SnippetCandidate(
        path=path,
        heading=heading,
        language=language,
        start_line=start_line,
        end_line=end_line,
        raw_excerpt=raw_excerpt,
        normalized_snippet=_truncate_text(_normalize_text(raw_excerpt), 260),
        matched_terms=excerpt_terms,
        exact_match_count=exact_match_count,
        keyword_coverage=keyword_coverage,
        file_role=file_role,
        is_source_file=is_source_file,
        symbol_match=symbol_match,
        search_rank=search_rank,
        relevance_score=relevance_score,
        why_it_matched=why_it_matched,
    )


def _merge_keyword_hits(
    grouped_hits: list[_SearchHit],
    keyword_queries: list[_KeywordQuery],
) -> dict[str, list[str]]:
    """把 display term 映射为用于本地匹配的 term 列表。"""

    fallback_map = {item.display_term: item.match_terms for item in keyword_queries}
    merged: dict[str, list[str]] = {}
    for hit in grouped_hits:
        merged[hit.display_term] = list(hit.match_terms or fallback_map.get(hit.display_term, []))
    return merged


def _find_line_hits(lines: list[str], keyword_map: dict[str, list[str]]) -> list[tuple[int, list[str]]]:
    """找到包含关键词的行，并记录命中的 display term。"""

    hits: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines, start=1):
        matched_terms: list[str] = []
        normalized_line = _normalize_for_match(line)
        if not normalized_line:
            continue
        for display_term, match_terms in keyword_map.items():
            if any(_normalize_for_match(term) in normalized_line for term in match_terms if term.strip()):
                matched_terms.append(display_term)
        if matched_terms:
            hits.append((index, _unique_list(matched_terms)))
    return hits


def _select_best_line_hit(
    lines: list[str],
    line_hits: list[tuple[int, list[str]]],
    keyword_map: dict[str, list[str]],
    *,
    path: str,
) -> tuple[int, list[str]]:
    """Pick the hit whose surrounding window looks most like business logic."""

    return max(
        line_hits,
        key=lambda item: (
            _score_candidate_window(lines, item[0], item[1], keyword_map, path=path),
            len(item[1]),
            _count_line_matches(lines[item[0] - 1], item[1], keyword_map),
            -item[0],
        ),
    )


def _score_candidate_window(
    lines: list[str],
    match_line: int,
    matched_terms: list[str],
    keyword_map: dict[str, list[str]],
    *,
    path: str = "",
) -> float:
    """Lightweight language-agnostic score for choosing useful code windows."""

    start_line, end_line, heading, symbol_match = _build_snippet_window(lines, match_line)
    excerpt = "\n".join(lines[start_line - 1 : end_line])
    normalized_excerpt = _normalize_for_match(excerpt)
    match_text = lines[match_line - 1] if 0 < match_line <= len(lines) else ""

    score = 0.0
    score += len(matched_terms) * 4.0
    score += _count_line_matches(match_text, matched_terms, keyword_map) * 1.5
    score += _count_regex_matches(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", excerpt) * 0.25
    score += sum(normalized_excerpt.count(term) for term in _BUSINESS_ACTION_TERMS) * 0.35
    score += sum(normalized_excerpt.count(term) for term in _CONTROL_FLOW_TERMS) * 0.45
    if symbol_match:
        score += 1.0
    if _is_low_value_anchor_line(match_text):
        score -= 2.0
    if _is_type_declaration_anchor_line(match_text):
        score -= 20.0
    if _is_field_declaration_anchor_line(match_text):
        score -= 12.0
    if _looks_like_dependency_injection_window(excerpt, heading):
        score -= 4.0
    if _looks_like_same_file_constructor_window(excerpt, heading, path):
        score -= 8.0
    if end_line - start_line + 1 < 6:
        score -= 1.5
    return score


def _count_regex_matches(pattern: str, text: str) -> int:
    """Count regex matches without leaking regex details into scoring code."""

    return len(re.findall(pattern, text))


def _is_type_declaration_anchor_line(line: str) -> bool:
    """Return whether a hit anchors on a broad type declaration."""

    stripped = line.strip()
    return bool(
        re.search(
            r"\b(class|interface|struct|trait|enum|type)\s+[A-Za-z_][A-Za-z0-9_]*",
            stripped,
        )
    )


def _is_field_declaration_anchor_line(line: str) -> bool:
    """Return whether a hit anchors on a field/property declaration."""

    stripped = line.strip()
    if "(" in stripped:
        return False
    return bool(
        re.search(
            r"\b(private|protected|public|readonly|final|static|val|var|let|const)\b",
            stripped,
        )
    )


def _looks_like_dependency_injection_window(excerpt: str, heading: str) -> bool:
    """Return whether a snippet mostly shows declarations or dependency injection."""

    lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
    if not lines:
        return False
    normalized_excerpt = _normalize_for_match(excerpt)
    assignment_lines = sum(
        1
        for line in lines
        if re.search(r"\b(this|self)\.[A-Za-z_][A-Za-z0-9_]*\s*=", line)
        or re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", line)
    )
    field_like_lines = sum(
        1
        for line in lines
        if re.search(r"\b(private|protected|public|readonly|final|val|var|let|const)\b", line)
        and "(" not in line
    )
    has_control_flow = any(term in normalized_excerpt for term in _CONTROL_FLOW_TERMS)
    has_business_call = _count_regex_matches(r"\.[A-Za-z_][A-Za-z0-9_]*\s*\(", excerpt) > 0
    mostly_assignments = (assignment_lines + field_like_lines) >= max(2, len(lines) // 2)
    heading_looks_constructor = bool(heading) and any(
        re.search(rf"\b{re.escape(heading)}\s*\(", line) for line in lines[:3]
    )
    return (mostly_assignments or heading_looks_constructor) and not has_control_flow and not has_business_call


def _looks_like_same_file_constructor_window(excerpt: str, heading: str, path: str) -> bool:
    """识别文件同名构造/初始化片段，避免把依赖装配当成业务主链路。"""

    file_stem = _fallback_heading_from_path(path)
    if not heading or not file_stem or _normalize_for_match(heading) != _normalize_for_match(file_stem):
        return False

    lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
    if not lines:
        return False

    head = "\n".join(lines[:4])
    if not re.search(rf"\b{re.escape(heading)}\s*\(", head):
        return False

    normalized_excerpt = _normalize_for_match(excerpt)
    assignment_lines = sum(
        1
        for line in lines
        if re.search(r"\b(this|self)\.[A-Za-z_][A-Za-z0-9_]*\s*=", line)
        or re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", line)
    )
    has_control_flow = any(term in normalized_excerpt for term in _CONTROL_FLOW_TERMS)
    return assignment_lines >= 2 and not has_control_flow


def _count_line_matches(line: str, display_terms: list[str], keyword_map: dict[str, list[str]]) -> int:
    """Count exact term occurrences in one line."""

    normalized_line = _normalize_for_match(line)
    total = 0
    for display_term in display_terms:
        for term in keyword_map.get(display_term, []):
            normalized_term = _normalize_for_match(term)
            if normalized_term:
                total += normalized_line.count(normalized_term)
    return total


def _is_low_value_anchor_line(line: str) -> bool:
    """Return whether a hit line is likely file header, import, annotation, or noise."""

    stripped = line.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if stripped in {"{", "}", "};"}:
        return True
    if lowered.startswith(("package ", "import ", "from ", "using ", "namespace ")):
        return True
    if stripped.startswith(("#", "//", "/*", "*", "@")):
        return True
    return False


def _build_snippet_window(lines: list[str], match_line: int) -> tuple[int, int, str, bool]:
    """围绕命中行截取 15-40 行的代码窗口，并尽量锚定到函数/类。"""

    symbol_line, heading = _find_nearest_symbol(lines, match_line)
    symbol_found = bool(symbol_line)
    start_line = symbol_line if symbol_found else max(1, match_line - _FALLBACK_LINE_PADDING)
    end_line = min(len(lines), max(start_line + _TARGET_SNIPPET_LINES - 1, match_line + _FALLBACK_LINE_PADDING))
    if symbol_found:
        next_symbol_line = _find_next_symbol_line(lines, symbol_line)
        if next_symbol_line:
            end_line = min(end_line, next_symbol_line - 1)
        end_line = max(end_line, match_line)
    end_line = min(end_line, start_line + _MAX_SNIPPET_LINES - 1)
    current_length = end_line - start_line + 1
    if current_length < _MIN_SNIPPET_LINES and not symbol_found:
        missing = _MIN_SNIPPET_LINES - current_length
        extend_down = min(len(lines) - end_line, missing)
        end_line += extend_down
        missing -= extend_down
        if missing > 0:
            start_line = max(1, start_line - missing)
    return start_line, end_line, heading, symbol_found


def _find_nearby_business_window(
    lines: list[str],
    start_line: int,
    end_line: int,
    heading: str,
    *,
    path: str,
) -> tuple[int, int, str, bool] | None:
    """声明/注入窗口质量低时，向下寻找更像业务逻辑的方法窗口。"""

    current_excerpt = "\n".join(lines[start_line - 1 : end_line])
    if not (
        _looks_like_dependency_injection_window(current_excerpt, heading)
        or _looks_like_same_file_constructor_window(current_excerpt, heading, path)
        or _is_type_declaration_anchor_line(lines[start_line - 1] if 0 < start_line <= len(lines) else "")
    ):
        return None

    best_window: tuple[float, int, int, str] | None = None
    search_end = min(len(lines), end_line + 80)
    for line_no in range(start_line + 1, search_end + 1):
        line = lines[line_no - 1]
        if _is_type_declaration_anchor_line(line) or _is_field_declaration_anchor_line(line):
            continue
        symbol_name = _match_symbol_name(line)
        if not symbol_name:
            continue
        candidate_start, candidate_end, candidate_heading, _ = _build_snippet_window(lines, line_no)
        if candidate_start == start_line and candidate_end == end_line:
            continue
        candidate_excerpt = "\n".join(lines[candidate_start - 1 : candidate_end])
        score = _score_business_density(candidate_excerpt)
        if score <= 0:
            continue
        candidate = (score, candidate_start, candidate_end, candidate_heading or symbol_name)
        if best_window is None or candidate > best_window:
            best_window = candidate

    if best_window is None:
        return None
    _, candidate_start, candidate_end, candidate_heading = best_window
    return candidate_start, candidate_end, candidate_heading, True


def _match_symbol_name(line: str) -> str:
    """Return the symbol name for one declaration line, if any."""

    for pattern in _SYMBOL_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group("name")
    return ""


def _score_business_density(excerpt: str) -> float:
    """Score whether a snippet contains real executable behavior."""

    normalized_excerpt = _normalize_for_match(excerpt)
    score = 0.0
    score += _count_regex_matches(r"\.[A-Za-z_][A-Za-z0-9_]*\s*\(", excerpt) * 1.2
    score += _count_regex_matches(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", excerpt) * 0.2
    score += sum(normalized_excerpt.count(term) for term in _BUSINESS_ACTION_TERMS) * 0.5
    score += sum(normalized_excerpt.count(term) for term in _CONTROL_FLOW_TERMS) * 0.7
    return score


def _find_nearest_symbol(lines: list[str], match_line: int) -> tuple[int, str]:
    """向上查找最近的函数、类或结构体定义。"""

    start_index = max(1, match_line - _MAX_SYMBOL_LOOKBACK)
    for line_no in range(match_line, start_index - 1, -1):
        line = lines[line_no - 1]
        for pattern in _SYMBOL_PATTERNS:
            match = pattern.search(line)
            if match:
                return line_no, match.group("name")
    return 0, ""


def _find_next_symbol_line(lines: list[str], symbol_line: int) -> int:
    """Find the next symbol after the current snippet anchor."""

    current_line = lines[symbol_line - 1]
    current_indent = len(current_line) - len(current_line.lstrip())
    for line_no in range(symbol_line + 1, len(lines) + 1):
        line = lines[line_no - 1]
        indent = len(line) - len(line.lstrip())
        if indent <= current_indent and any(pattern.search(line) for pattern in _SYMBOL_PATTERNS):
            return line_no
    return 0


def _trim_leading_non_body_lines(lines: list[str], start_line: int, end_line: int) -> int:
    """Drop leading package/import/comment lines when no symbol anchor is available."""

    current = start_line
    while current < end_line and _is_low_value_anchor_line(lines[current - 1]):
        current += 1
    return current


def _fallback_heading_from_path(path: str) -> str:
    """Build a stable non-empty heading when no symbol can be found."""

    file_name = path.rsplit("/", 1)[-1].strip()
    if not file_name:
        return "code_snippet"
    if "." not in file_name or file_name.lower() == "dockerfile":
        return file_name
    return file_name.rsplit(".", 1)[0] or file_name


def _collect_excerpt_terms(raw_excerpt: str, keyword_map: dict[str, list[str]]) -> list[str]:
    """收集片段中真实命中的 display term。"""

    normalized_excerpt = _normalize_for_match(raw_excerpt)
    matched_terms: list[str] = []
    for display_term, match_terms in keyword_map.items():
        if any(_normalize_for_match(term) in normalized_excerpt for term in match_terms if term.strip()):
            matched_terms.append(display_term)
    return _unique_list(matched_terms)


def _count_exact_matches(raw_excerpt: str, display_terms: list[str], keyword_map: dict[str, list[str]]) -> int:
    """统计片段中的关键词精确命中次数。"""

    normalized_excerpt = _normalize_for_match(raw_excerpt)
    total = 0
    for display_term in display_terms:
        for term in keyword_map.get(display_term, []):
            normalized_term = _normalize_for_match(term)
            if not normalized_term:
                continue
            total += normalized_excerpt.count(normalized_term)
    return total


def _build_why_it_matched(
    *,
    path: str,
    heading: str,
    matched_terms: list[str],
    start_line: int,
    end_line: int,
    fallback_search_terms: list[str] | None = None,
) -> str:
    """生成片段命中原因。"""

    parts = [f"命中关键词：{', '.join(matched_terms) if matched_terms else '无'}"]
    if fallback_search_terms:
        parts.append(f"备用查询：{', '.join(fallback_search_terms)}")
    if heading:
        parts.append(f"符号：{heading}")
    parts.append(f"位置：{path}:{start_line}-{end_line}")
    return "；".join(parts)


def _collect_used_fallback_search_terms(grouped_hits: list[_SearchHit]) -> list[str]:
    """提取实际触发命中的备用查询词。"""

    fallback_terms: list[str] = []
    for hit in grouped_hits:
        if _normalize_for_match(hit.search_term) == _normalize_for_match(hit.display_term):
            continue
        fallback_terms.append(hit.search_term)
    return _unique_list(fallback_terms)


def _select_top_items(candidates: list[_SnippetCandidate], *, top_k: int) -> list[GitHubRepoEvidenceItem]:
    """按固定排序与路径去重选择最终代码片段卡。"""

    items: list[GitHubRepoEvidenceItem] = []
    seen_paths: set[str] = set()
    seen_windows: set[tuple[str, int, int]] = set()
    deferred: list[_SnippetCandidate] = []
    for candidate in candidates:
        window_key = (candidate.path.lower(), candidate.start_line, candidate.end_line)
        if window_key in seen_windows:
            continue
        lowered_path = candidate.path.lower()
        if lowered_path in seen_paths:
            deferred.append(candidate)
            continue
        seen_windows.add(window_key)
        seen_paths.add(lowered_path)
        items.append(_to_result_item(candidate))
        if len(items) >= top_k:
            return items

    for candidate in deferred:
        window_key = (candidate.path.lower(), candidate.start_line, candidate.end_line)
        if window_key in seen_windows:
            continue
        seen_windows.add(window_key)
        items.append(_to_result_item(candidate))
        if len(items) >= top_k:
            break
    return items


def _to_result_item(candidate: _SnippetCandidate) -> GitHubRepoEvidenceItem:
    """把内部候选转成公开返回结构。"""

    return GitHubRepoEvidenceItem(
        evidence_type="code_snippet",
        source_path=candidate.path,
        heading=candidate.heading,
        raw_excerpt=candidate.raw_excerpt,
        normalized_snippet=candidate.normalized_snippet,
        matched_terms=candidate.matched_terms,
        relevance_score=candidate.relevance_score,
        why_it_matched=candidate.why_it_matched,
        language=candidate.language,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
    )


def _build_search_query(repo_name: str, search_term: str) -> str:
    """构造 repo 限定的 search_code 查询。"""

    normalized_term = search_term.strip()
    if " " in normalized_term:
        normalized_term = f"\"{normalized_term}\""
    return f"{normalized_term} repo:{repo_name}"


def _parse_search_code_matches(payload: Any) -> list[_SearchCodeMatch]:
    """解析 search_code 的 MCP 返回。"""

    raw_text = _extract_primary_text(payload)
    if not raw_text.strip():
        return []

    parsed_items: list[Any] = []
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            parsed_items = items
        elif isinstance(data.get("results"), list):
            parsed_items = data["results"]
    elif isinstance(data, list):
        parsed_items = data

    matches: list[_SearchCodeMatch] = []
    if parsed_items:
        for index, item in enumerate(parsed_items):
            path = _extract_path_from_search_item(item)
            if not path:
                continue
            matches.append(_SearchCodeMatch(path=path, result_order=index))
        return matches

    fallback_paths = _extract_paths_from_text(raw_text)
    return [_SearchCodeMatch(path=path, result_order=index) for index, path in enumerate(fallback_paths)]


def _extract_path_from_search_item(item: Any) -> str:
    """从 search_code 单条结果中提取路径。"""

    if not isinstance(item, dict):
        return ""
    direct_path = item.get("path")
    if isinstance(direct_path, str) and direct_path.strip():
        return direct_path.strip()

    file_info = item.get("file")
    if isinstance(file_info, dict):
        file_path = file_info.get("path") or file_info.get("name")
        if isinstance(file_path, str) and file_path.strip():
            return file_path.strip()

    text_match_path = item.get("file_path") or item.get("name")
    if isinstance(text_match_path, str) and text_match_path.strip():
        return text_match_path.strip()

    return ""


def _extract_paths_from_text(text: str) -> list[str]:
    """在非 JSON 文本里兜底提取可识别的文件路径。"""

    path_pattern = re.compile(
        r"(?:^|\s)(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+"
        r"(?:\.(?:py|ts|tsx|js|jsx|java|go|rs|cpp|c|h|hpp|kt|scala|ya?ml|toml))?|Dockerfile)",
        re.IGNORECASE,
    )
    paths = [match.group("path").strip() for match in path_pattern.finditer(text)]
    return _unique_list(paths)


def _select_readme_fallback_section(
    readme_content: str,
    keyword_queries: list[_KeywordQuery],
) -> tuple[str, str, list[str], list[str]] | None:
    """选择 README 中关键词覆盖最多的短段落。"""

    blocks = _split_markdown_blocks(readme_content)
    ranked_blocks: list[tuple[int, int, int, str, str, list[str], list[str]]] = []
    for index, (heading, block_text) in enumerate(blocks):
        matched_terms, matched_search_terms = _match_readme_terms(block_text, keyword_queries)
        if not matched_terms:
            continue
        normalized_block = _normalize_text(block_text)
        ranked_blocks.append(
            (
                -len(matched_terms),
                -len(matched_search_terms),
                index,
                heading,
                normalized_block,
                matched_terms,
                matched_search_terms,
            )
        )
    if not ranked_blocks:
        return None

    _, _, _, heading, normalized_block, matched_terms, matched_search_terms = sorted(ranked_blocks)[0]
    excerpt = _truncate_text(normalized_block, 900)
    return heading or "README", excerpt, matched_terms, matched_search_terms


def _split_markdown_blocks(readme_content: str) -> list[tuple[str, str]]:
    """按标题与段落粗分 README，避免把整篇文档塞给模型。"""

    blocks: list[tuple[str, str]] = []
    current_heading = "README"
    current_lines: list[str] = []
    for line in readme_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current_lines:
                blocks.append((current_heading, "\n".join(current_lines)))
                current_lines = []
            current_heading = stripped.lstrip("#").strip() or "README"
            continue
        if not stripped:
            if current_lines:
                blocks.append((current_heading, "\n".join(current_lines)))
                current_lines = []
            continue
        current_lines.append(stripped)
    if current_lines:
        blocks.append((current_heading, "\n".join(current_lines)))
    if not blocks and readme_content.strip():
        blocks.append(("README", readme_content.strip()))
    return blocks


def _match_readme_terms(
    text: str,
    keyword_queries: list[_KeywordQuery],
) -> tuple[list[str], list[str]]:
    """返回 README 段落里命中的原始关键词和实际命中词。"""

    normalized_text = _normalize_for_match(text)
    matched_terms: list[str] = []
    matched_search_terms: list[str] = []
    for query in keyword_queries:
        search_terms = _unique_list(
            [
                *query.match_terms,
                query.search_term,
                *query.fallback_search_terms,
            ]
        )
        actual_terms = [
            term
            for term in search_terms
            if term.strip() and _normalize_for_match(term) in normalized_text
        ]
        if actual_terms:
            matched_terms.append(query.display_term)
            matched_search_terms.extend(actual_terms)
    return _unique_list(matched_terms), _unique_list(matched_search_terms)


def _build_readme_why_it_matched(*, matched_terms: list[str], matched_search_terms: list[str]) -> str:
    """生成 README 兜底命中说明。"""

    parts = [f"命中关键词：{', '.join(matched_terms) if matched_terms else '无'}"]
    if matched_search_terms:
        parts.append(f"README 实际命中：{', '.join(matched_search_terms)}")
    parts.append("代码搜索为空，使用 README 作为 GitHub 追问兜底证据")
    return "；".join(parts)


def _build_match_terms(keyword: str) -> list[str]:
    """把关键技术词扩展成更适合代码匹配的 term。"""

    normalized_keyword = keyword.strip()
    if len(normalized_keyword) < 2:
        return []
    return [normalized_keyword]


def _build_fallback_search_terms(keyword: str) -> list[str]:
    """为 LLM 显式关键词生成有限备用查询词。"""

    normalized_keyword = keyword.strip()
    if len(normalized_keyword) < 2:
        return []

    candidates: list[str] = []
    lowered_compact = re.sub(r"[\s_.#/+:-]+", "", normalized_keyword).lower()
    if "redisstream" in lowered_compact:
        candidates.extend(["Redis Stream", "Redis", "Stream"])
    if "springai" in lowered_compact:
        candidates.extend(["Spring AI", "spring-ai", "Spring"])
    if "rag" in lowered_compact:
        candidates.extend(["RAG", "VectorService", "KnowledgeBase", "PgVectorStore"])
    if "followup" in lowered_compact:
        candidates.extend(["followUp", "followUps", "InterviewQuestion", "QuestionService"])
    if lowered_compact.endswith("service") and len(normalized_keyword) > len("Service"):
        service_stem = re.sub(r"(?i)service$", "", normalized_keyword).strip()
        if service_stem:
            candidates.append(service_stem)

    words = _split_keyword_words(normalized_keyword)
    if len(words) >= 2:
        candidates.append(" ".join(words))
        candidates.extend([word for word in words if len(word) >= 3 and word.lower() not in _GENERIC_FALLBACK_WORDS])

    return _unique_list(
        [
            candidate
            for candidate in candidates
            if _normalize_for_match(candidate) != _normalize_for_match(normalized_keyword)
        ]
    )


def _split_keyword_words(keyword: str) -> list[str]:
    """拆分空格、连接符和 CamelCase 关键词。"""

    normalized = re.sub(r"[_./+-]+", " ", keyword.strip())
    raw_parts = [part for part in normalized.split() if part]
    words: list[str] = []
    for part in raw_parts:
        camel_parts = _CAMEL_CASE_PATTERN.findall(part)
        words.extend(camel_parts or [part])
    return [word for word in words if word]


def _pick_search_term(keyword: str, match_terms: list[str]) -> str:
    """挑选最适合 search_code 的查询词。"""

    if match_terms:
        return match_terms[0]
    return keyword.strip()


def _classify_file_kind(path: str) -> str:
    """识别命中文件是业务源码、支撑源码、配置，还是应忽略。"""

    lowered_path = path.lower()
    file_name = lowered_path.rsplit("/", 1)[-1]
    if any(lowered_path.endswith(extension) for extension in _SOURCE_EXTENSIONS):
        return "supporting_source" if _is_supporting_source_path(lowered_path, file_name) else "implementation_source"
    if file_name in _CONFIG_FILENAMES or any(lowered_path.endswith(extension) for extension in _CONFIG_EXTENSIONS):
        return "config"
    return "ignore"


def _is_supporting_source_path(lowered_path: str, file_name: str) -> bool:
    """识别配置、接入、工具类源码；common 单独出现不作为降级信号。"""

    stem = file_name.rsplit(".", 1)[0]
    path_segments = [segment for segment in lowered_path.split("/")[:-1] if segment]
    if any(segment in _SUPPORTING_SOURCE_PATH_SEGMENTS for segment in path_segments):
        return True
    return any(term in stem for term in _SUPPORTING_SOURCE_NAME_TERMS)


def _detect_language(path: str) -> str | None:
    """根据文件路径猜测语言。"""

    lowered_path = path.lower()
    file_name = lowered_path.rsplit("/", 1)[-1]
    if file_name == "dockerfile":
        return "dockerfile"
    for extension, language in _LANGUAGE_BY_EXTENSION.items():
        if lowered_path.endswith(extension):
            return language
    return None


def _extract_primary_text(payload: Any) -> str:
    """从 MCP 返回里提取主要文本内容。"""

    if isinstance(payload, str):
        return payload

    text_parts: list[str] = []
    iterable: list[Any]
    if isinstance(payload, list):
        iterable = payload
    elif isinstance(payload, dict):
        iterable = [payload]
    else:
        iterable = []

    for item in iterable:
        text_value = None
        if isinstance(item, dict):
            text_value = item.get("text")
        elif hasattr(item, "text"):
            text_value = getattr(item, "text", None)
        if isinstance(text_value, str) and text_value.strip():
            text_parts.append(text_value)

    if not text_parts:
        return ""

    json_like_parts = [part for part in text_parts if part.lstrip().startswith(("[", "{"))]
    if json_like_parts:
        return max(json_like_parts, key=len)
    return max(text_parts, key=len)


def _normalize_text(text: str) -> str:
    """压缩多行文本为单行摘要。"""

    normalized_parts = [part.strip() for part in text.splitlines() if part.strip()]
    return re.sub(r"\s+", " ", " ".join(normalized_parts)).strip()


def _truncate_text(text: str, max_length: int) -> str:
    """截断长文本，便于在 prompt 里展示。"""

    normalized = _normalize_text(text)
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + "..."


def _normalize_for_match(text: str) -> str:
    """统一小写并压缩空白，便于做 substring 匹配。"""

    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return normalized


def _build_matched_files(items: list[GitHubRepoEvidenceItem]) -> list[str]:
    """提取命中的文件路径列表。"""

    return _unique_list([item.source_path for item in items if item.source_path.strip()])


def _unique_list(values: list[str]) -> list[str]:
    """按顺序去重。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(normalized)
    return result


__all__ = [
    "GitHubRepoEvidenceItem",
    "GitHubRepoEvidenceToolInput",
    "GitHubRepoEvidenceToolResult",
    "github_repo_evidence_tool",
]
