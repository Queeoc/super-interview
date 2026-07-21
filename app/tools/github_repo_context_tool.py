"""GitHub 仓库上下文提取工具。"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.agent.mcp_client import get_mcp_client_with_retry

_GITHUB_URL_PATTERN = re.compile(
    r"(https?://)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(?P<title>.+?)\s*$")
_NOISE_LINE_PREFIXES = ("[![", "![", "<img", "https://img.shields.io/")
_README_CANDIDATE_PATHS = (
    "README.md",
    "README.MD",
    "readme.md",
    "README_CN.md",
    "README.zh-CN.md",
)
_KEY_FILE_PRIORITY = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "pom.xml",
    "Dockerfile",
)
_SUCCESS_TEXT_PREFIXES = (
    "successfully downloaded",
    "successfully fetched",
)
_SECTION_TYPES = (
    "project_summary",
    "tech_stack",
    "module_structure",
    "implementation_anchor",
    "maintenance_signal",
)
_SECTION_TYPE_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "project_summary": {
        "title": ("简介", "overview", "introduction", "features", "feature", "功能", "特性"),
        "body": ("项目", "平台", "系统", "feature", "solution", "problem", "场景", "能力"),
    },
    "tech_stack": {
        "title": ("技术栈", "tech stack", "dependencies", "requirements"),
        "body": (
            "spring",
            "fastapi",
            "django",
            "react",
            "vue",
            "postgresql",
            "mysql",
            "redis",
            "docker",
            "kafka",
            "技术栈",
            "数据库",
            "中间件",
        ),
    },
    "module_structure": {
        "title": ("架构", "architecture", "模块", "structure", "系统设计"),
        "body": (
            "模块",
            "目录",
            "结构",
            "组件",
            "frontend",
            "backend",
            "service",
            "api",
            "worker",
            "前端",
            "后端",
            "服务",
        ),
    },
    "implementation_anchor": {
        "title": ("实现", "workflow", "pipeline", "usage", "deployment", "安装", "部署", "运行"),
        "body": (
            "workflow",
            "pipeline",
            "deploy",
            "docker compose",
            "运行",
            "部署",
            "调用",
            "流程",
            "安装",
            "usage",
        ),
    },
    "maintenance_signal": {
        "title": ("roadmap", "todo", "更新", "release", "changelog", "维护", "迭代"),
        "body": (
            "持续",
            "维护",
            "更新",
            "迭代",
            "roadmap",
            "todo",
            "release",
            "changelog",
            "计划",
            "长期",
        ),
    },
}
_MODULE_HINT_TERMS = (
    "模块",
    "目录",
    "结构",
    "组件",
    "frontend",
    "backend",
    "service",
    "api",
    "worker",
    "前端",
    "后端",
    "服务",
    "架构",
)
_TECH_HINT_TERMS = (
    "spring",
    "fastapi",
    "django",
    "flask",
    "react",
    "vue",
    "postgresql",
    "mysql",
    "redis",
    "docker",
    "kafka",
    "rabbitmq",
    "minio",
    "nginx",
    "java",
    "python",
    "go",
    "typescript",
)
_README_NOISE_TERMS = (
    "star history",
    "license",
    "sponsor",
    "badge",
    "截图",
)


class GitHubRepoContextToolInput(BaseModel):
    """GitHub 仓库上下文工具输入。"""

    model_config = ConfigDict(extra="forbid")

    resume_markdown: str = Field(default="", description="简历 Markdown 内容")
    resume_metadata: dict[str, Any] = Field(default_factory=dict, description="简历元数据")
    question_text: str = Field(default="", description="当前问题")
    answer_text: str = Field(default="", description="候选人最近回答")
    focus_topics: list[str] = Field(default_factory=list, description="追问重点主题")
    missing_signals: list[str] = Field(default_factory=list, description="当前缺失信号")


class GitHubRepoTreeEntry(BaseModel):
    """过滤后的仓库目录项。"""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="file 或 dir")
    name: str = Field(..., description="目录或文件名")
    path: str = Field(..., description="路径")
    size: int | None = Field(default=None, description="文件大小")


class GitHubRepoFileSummary(BaseModel):
    """关键文件摘要。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="文件路径")
    summary: str = Field(..., description="清洗后的文件摘要")
    file_role: str = Field(..., description="文件作用类别")


class GitHubReadmeSection(BaseModel):
    """README 命中的结构化章节。"""

    model_config = ConfigDict(extra="forbid")

    heading: str = Field(default="", description="章节标题")
    level: int = Field(default=0, description="标题层级，README_INTRO 为 0")
    section_type: str = Field(default="project_summary", description="章节分类")
    raw_excerpt: str = Field(default="", description="原始章节片段")
    normalized_snippet: str = Field(default="", description="规整后的章节摘要")
    matched_terms: list[str] = Field(default_factory=list, description="命中的关键术语")
    relevance_score: float = Field(default=0.0, description="面向当前问题的相关分")


class GitHubRepoContextToolResult(BaseModel):
    """GitHub 仓库上下文工具输出。"""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(default=False, description="是否成功获取仓库上下文")
    repo_url: str = Field(default="", description="匹配到的仓库链接")
    repo_name: str = Field(default="", description="owner/repo")
    matched_project_title: str = Field(default="", description="命中的简历项目标题")
    matched_project_excerpt: str = Field(default="", description="命中的简历项目片段")
    structure_entries: list[GitHubRepoTreeEntry] = Field(default_factory=list, description="根目录摘要")
    readme_summary: str = Field(default="", description="README 兼容摘要，等于 readme_intro")
    readme_intro: str = Field(default="", description="README 项目简介摘要")
    readme_sections: list[GitHubReadmeSection] = Field(default_factory=list, description="README 命中章节")
    key_file_summaries: list[GitHubRepoFileSummary] = Field(default_factory=list, description="关键文件摘要")
    retrieval_reason: str = Field(default="", description="检索结果说明")
    error_message: str | None = Field(default=None, description="错误信息")


class GitHubRepoBindingResult(BaseModel):
    """简历项目与 GitHub 仓库的轻量绑定结果。"""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(default=False)
    repo_url: str = Field(default="")
    repo_name: str = Field(default="")
    owner: str = Field(default="")
    repo: str = Field(default="")
    matched_project_title: str = Field(default="")
    matched_project_excerpt: str = Field(default="")
    retrieval_reason: str = Field(default="")


class _ResumeSection(BaseModel):
    """简历项目分段。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    excerpt: str
    github_links: list[str]
    score: float = 0.0


class _RawReadmeSection(BaseModel):
    """README 原始章节。"""

    model_config = ConfigDict(extra="forbid")

    heading: str
    level: int
    lines: list[str]


async def github_repo_context_tool(**kwargs: Any) -> GitHubRepoContextToolResult:
    """从简历中绑定一个 GitHub 仓库并读取轻量上下文。"""

    tool_input = GitHubRepoContextToolInput.model_validate(kwargs)
    logger.info(
        "github repo context retrieval started question_preview={}, focus_topics={}, missing_signals={}",
        tool_input.question_text[:80],
        tool_input.focus_topics[:4],
        tool_input.missing_signals[:4],
    )
    resume_markdown = tool_input.resume_markdown.strip()
    if not resume_markdown:
        return GitHubRepoContextToolResult(found=False, retrieval_reason="resume_missing")

    binding = resolve_github_repo_binding(
        resume_markdown=resume_markdown,
        question_text=tool_input.question_text,
        answer_text=tool_input.answer_text,
        focus_topics=tool_input.focus_topics,
        missing_signals=tool_input.missing_signals,
    )
    if not binding.found:
        return GitHubRepoContextToolResult(
            found=False,
            repo_url=binding.repo_url,
            repo_name=binding.repo_name,
            matched_project_title=binding.matched_project_title,
            matched_project_excerpt=binding.matched_project_excerpt,
            retrieval_reason=binding.retrieval_reason,
        )
    owner = binding.owner
    repo = binding.repo
    repo_name = binding.repo_name

    try:
        root_payload = await _invoke_github_get_file_contents(owner=owner, repo=repo, path="")
        root_entries = _parse_directory_entries(root_payload)
        readme_content = await _load_readme_content(owner=owner, repo=repo)
        readme_intro, readme_sections = _extract_readme_context(
            readme_content,
            question_text=tool_input.question_text,
            answer_text=tool_input.answer_text,
            focus_topics=tool_input.focus_topics,
            missing_signals=tool_input.missing_signals,
        )
        key_file_summaries = await _load_key_file_summaries(owner=owner, repo=repo, root_entries=root_entries)
    except Exception as exc:
        logger.warning("github repo context tool failed repo_name={}, error={}", repo_name, exc)
        return GitHubRepoContextToolResult(
            found=False,
            repo_url=binding.repo_url,
            repo_name=repo_name,
            matched_project_title=binding.matched_project_title,
            matched_project_excerpt=binding.matched_project_excerpt,
            retrieval_reason="github_repo_fetch_failed",
            error_message=str(exc),
        )

    found = bool(readme_intro.strip() or readme_sections or root_entries or key_file_summaries)
    retrieval_reason = "github_repo_context_ready" if found else "github_repo_context_empty"
    logger.info(
        "github repo context retrieval completed found={}, repo_name={}, readme_sections={}, structure_count={}, key_file_count={}, retrieval_reason={}",
        found,
        repo_name,
        len(readme_sections),
        len(root_entries),
        len(key_file_summaries),
        retrieval_reason,
    )
    return GitHubRepoContextToolResult(
        found=found,
        repo_url=binding.repo_url,
        repo_name=repo_name,
        matched_project_title=binding.matched_project_title,
        matched_project_excerpt=binding.matched_project_excerpt,
        structure_entries=root_entries,
        readme_summary=readme_intro,
        readme_intro=readme_intro,
        readme_sections=readme_sections,
        key_file_summaries=key_file_summaries,
        retrieval_reason=retrieval_reason,
        error_message=None,
    )


def resolve_github_repo_binding(
    *,
    resume_markdown: str,
    question_text: str,
    answer_text: str,
    focus_topics: list[str],
    missing_signals: list[str],
) -> GitHubRepoBindingResult:
    """从简历中选择与当前追问最相关的 GitHub 仓库绑定。"""

    normalized_resume = resume_markdown.strip()
    if not normalized_resume:
        return GitHubRepoBindingResult(found=False, retrieval_reason="resume_missing")

    sections = _extract_resume_sections(normalized_resume)
    if not sections:
        return GitHubRepoBindingResult(found=False, retrieval_reason="resume_section_missing")

    selected_section = _select_best_section(
        sections,
        question_text=question_text,
        answer_text=answer_text,
        focus_topics=focus_topics,
        missing_signals=missing_signals,
    )
    if selected_section is None or not selected_section.github_links:
        return GitHubRepoBindingResult(found=False, retrieval_reason="github_link_missing")

    repo_url = selected_section.github_links[0]
    repo_match = _GITHUB_URL_PATTERN.search(repo_url)
    if repo_match is None:
        return GitHubRepoBindingResult(
            found=False,
            repo_url=repo_url,
            matched_project_title=selected_section.title,
            matched_project_excerpt=selected_section.excerpt,
            retrieval_reason="github_link_invalid",
        )

    owner = repo_match.group("owner")
    repo = repo_match.group("repo")
    return GitHubRepoBindingResult(
        found=True,
        repo_url=repo_url,
        repo_name=f"{owner}/{repo}",
        owner=owner,
        repo=repo,
        matched_project_title=selected_section.title,
        matched_project_excerpt=selected_section.excerpt,
        retrieval_reason="github_repo_binding_ready",
    )


def _extract_resume_sections(resume_markdown: str) -> list[_ResumeSection]:
    """按 Markdown 标题切分简历，并收集其中的 GitHub 链接。"""

    lines = [line.rstrip() for line in resume_markdown.splitlines()]
    sections: list[_ResumeSection] = []
    current_title = "简历概览"
    buffer: list[str] = []

    def _flush_buffer() -> None:
        nonlocal buffer
        normalized_lines = [line.strip() for line in buffer if line.strip()]
        if not normalized_lines:
            buffer = []
            return
        excerpt = "\n".join(normalized_lines)
        github_links = _extract_github_links(excerpt)
        sections.append(
            _ResumeSection(
                title=current_title,
                excerpt=excerpt,
                github_links=github_links,
            )
        )
        buffer = []

    for line in lines:
        header_match = _HEADER_PATTERN.match(line.strip())
        if header_match:
            _flush_buffer()
            current_title = header_match.group("title").strip()
            continue
        if not line.strip():
            _flush_buffer()
            continue
        buffer.append(line)

    _flush_buffer()
    return sections


def _extract_github_links(text: str) -> list[str]:
    """提取并去重 GitHub 仓库链接。"""

    links: list[str] = []
    seen: set[str] = set()
    for match in _GITHUB_URL_PATTERN.finditer(text):
        owner = match.group("owner")
        repo = match.group("repo").rstrip(".),]")
        normalized = f"https://github.com/{owner}/{repo}"
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


def _select_best_section(
    sections: list[_ResumeSection],
    *,
    question_text: str,
    answer_text: str,
    focus_topics: list[str],
    missing_signals: list[str],
) -> _ResumeSection | None:
    """选择与当前问题最相关的简历项目段落。"""

    query_terms = _build_query_terms(
        question_text=question_text,
        answer_text=answer_text,
        focus_topics=focus_topics,
        missing_signals=missing_signals,
    )
    scored_sections: list[_ResumeSection] = []
    for section in sections:
        if not section.github_links:
            continue
        score = _score_text_match(section.excerpt, query_terms) + (0.25 if "项目" in section.title else 0.0)
        scored_sections.append(section.model_copy(update={"score": score}))

    if not scored_sections:
        return None
    return max(scored_sections, key=lambda item: (item.score, len(item.excerpt)))


def _build_query_terms(
    *,
    question_text: str,
    answer_text: str,
    focus_topics: list[str],
    missing_signals: list[str],
) -> list[str]:
    """构造用于段落选择和证据匹配的词项。"""

    raw_terms: list[str] = []
    raw_terms.extend(_extract_terms(question_text))
    raw_terms.extend(_extract_terms(answer_text))
    for item in focus_topics:
        raw_terms.extend(_extract_terms(item))
        raw_terms.extend(_expand_query_term(item))
    for item in missing_signals:
        raw_terms.extend(_extract_terms(item))
        raw_terms.extend(_expand_query_term(item))
    return _unique_list(raw_terms)


def _extract_terms(text: str) -> list[str]:
    """从中英文文本中提取简单词项。"""

    if not text.strip():
        return []
    lowered = text.lower()
    ascii_terms = re.findall(r"[a-z0-9#+./_-]{2,}", lowered)
    chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,12}", text)
    return ascii_terms + chinese_terms


def _expand_query_term(text: str) -> list[str]:
    """为中文短语补充较短词项，提升匹配泛化性。"""

    stripped = text.strip().lower()
    if not stripped:
        return []
    if re.fullmatch(r"[\u4e00-\u9fff]{4,10}", stripped) is None:
        return [stripped]
    variants = [stripped]
    if len(stripped) >= 2:
        variants.append(stripped[:2])
        variants.append(stripped[-2:])
    if len(stripped) >= 4:
        variants.append(stripped[:4])
        variants.append(stripped[-4:])
    for index in range(0, len(stripped) - 1):
        variants.append(stripped[index : index + 2])
    for index in range(0, len(stripped) - 2):
        variants.append(stripped[index : index + 3])
    return _unique_list(variants)


def _score_text_match(text: str, query_terms: list[str]) -> float:
    """基于词项命中计算文本相关分。"""

    haystack = text.lower()
    score = 0.0
    for term in query_terms:
        if term and term in haystack:
            score += 0.18 if len(term) >= 4 else 0.12
    return score


async def _load_readme_content(*, owner: str, repo: str) -> str:
    """读取 README 原文。"""

    for candidate in _README_CANDIDATE_PATHS:
        try:
            payload = await _invoke_github_get_file_contents(owner=owner, repo=repo, path=candidate)
        except Exception:
            continue
        content = _extract_primary_text(payload)
        if content.strip():
            return content
    return ""


def _extract_readme_context(
    readme_content: str,
    *,
    question_text: str,
    answer_text: str,
    focus_topics: list[str],
    missing_signals: list[str],
) -> tuple[str, list[GitHubReadmeSection]]:
    """解析 README 并抽取高价值章节。"""

    if not readme_content.strip():
        return "", []

    cleaned_lines = _clean_readme_lines(readme_content)
    raw_sections = _split_readme_sections(cleaned_lines)
    if not raw_sections:
        return "", []

    readme_intro = _extract_readme_intro(raw_sections)
    question_terms = _unique_list(_extract_terms(question_text))
    missing_terms = _build_signal_terms(missing_signals)
    focus_terms = _build_signal_terms(focus_topics)
    preferred_types = _build_preferred_type_sets(
        question_text=question_text,
        focus_topics=focus_topics,
        missing_signals=missing_signals,
    )

    scored_sections: list[GitHubReadmeSection] = []
    for raw_section in raw_sections:
        if raw_section.heading == "README_INTRO":
            continue
        body_text = "\n".join(raw_section.lines).strip()
        if not body_text:
            continue
        normalized_snippet = _truncate_text(_normalize_text(body_text), 320)
        if not normalized_snippet:
            continue
        section_type = _classify_readme_section(raw_section.heading, body_text)
        matched_terms = _collect_matched_terms(
            body_text,
            missing_terms=missing_terms,
            focus_terms=focus_terms,
            question_terms=question_terms,
        )
        relevance_score = _score_readme_section(
            heading=raw_section.heading,
            body_text=body_text,
            section_type=section_type,
            level=raw_section.level,
            missing_terms=missing_terms,
            focus_terms=focus_terms,
            question_terms=question_terms,
            preferred_types=preferred_types,
            matched_terms=matched_terms,
        )
        scored_sections.append(
            GitHubReadmeSection(
                heading=raw_section.heading,
                level=raw_section.level,
                section_type=section_type,
                raw_excerpt=body_text,
                normalized_snippet=normalized_snippet,
                matched_terms=matched_terms,
                relevance_score=round(relevance_score, 3),
            )
        )

    selected_sections = _select_top_readme_sections(scored_sections)
    return readme_intro, selected_sections


def _clean_readme_lines(content: str) -> list[str]:
    """清洗 README 噪音，保留标题、正文和列表。"""

    stripped_content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    lines: list[str] = []
    in_code_block = False
    for raw_line in stripped_content.splitlines():
        line = raw_line.rstrip()
        fence = line.strip()
        if fence.startswith("```") or fence.startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if any(fence.startswith(prefix) for prefix in _NOISE_LINE_PREFIXES):
            continue
        if re.fullmatch(r"https?://\S+", fence):
            continue
        if re.fullmatch(r"[-*+]\s*\[.+\]\(#.+\)", fence, flags=re.IGNORECASE):
            continue

        cleaned_line = _strip_inline_html(line).strip()
        if not cleaned_line:
            lines.append("")
            continue

        if cleaned_line.startswith("![") or cleaned_line.startswith("[!["):
            continue
        lines.append(cleaned_line)

    return _collapse_blank_lines(lines)


def _strip_inline_html(text: str) -> str:
    """移除内联 HTML 标签，保留可见文本。"""

    without_tags = re.sub(r"</?[^>]+>", " ", text)
    return re.sub(r"\s+", " ", without_tags).strip()


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    """合并连续空行。"""

    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = is_blank
    return collapsed


def _split_readme_sections(lines: list[str]) -> list[_RawReadmeSection]:
    """按标题切分 README，`#` 到 `###` 为主要分段边界。"""

    sections: list[_RawReadmeSection] = []
    intro_lines: list[str] = []
    current_heading = ""
    current_level = 0
    current_lines: list[str] = []

    def _flush_current() -> None:
        nonlocal current_heading, current_level, current_lines
        normalized_lines = [line for line in current_lines if line.strip()]
        if current_heading and normalized_lines:
            sections.append(
                _RawReadmeSection(
                    heading=current_heading,
                    level=current_level,
                    lines=normalized_lines,
                )
            )
        current_heading = ""
        current_level = 0
        current_lines = []

    for line in lines:
        stripped = line.strip()
        header_match = _HEADER_PATTERN.match(stripped)
        if header_match:
            level = len(header_match.group(1))
            title = header_match.group("title").strip()
            if level <= 3:
                if not current_heading and intro_lines:
                    sections.append(
                        _RawReadmeSection(
                            heading="README_INTRO",
                            level=0,
                            lines=[item for item in intro_lines if item.strip()],
                        )
                    )
                    intro_lines = []
                _flush_current()
                current_heading = title
                current_level = level
                continue
            if current_heading:
                current_lines.append(title)
            else:
                intro_lines.append(title)
            continue

        if not current_heading:
            intro_lines.append(stripped)
            continue
        current_lines.append(stripped)

    if not current_heading and intro_lines:
        sections.append(
            _RawReadmeSection(
                heading="README_INTRO",
                level=0,
                lines=[item for item in intro_lines if item.strip()],
            )
        )
    _flush_current()
    return sections


def _extract_readme_intro(raw_sections: list[_RawReadmeSection]) -> str:
    """提取 README 的简介摘要。"""

    for section in raw_sections:
        if section.heading == "README_INTRO" and section.lines:
            return _truncate_text(_normalize_text("\n".join(section.lines)), 320)
    for section in raw_sections:
        if section.lines:
            return _truncate_text(_normalize_text("\n".join(section.lines)), 320)
    return ""


def _normalize_text(text: str) -> str:
    """压缩多行文本为空格分隔形式。"""

    normalized_parts = [part.strip() for part in text.splitlines() if part.strip()]
    return re.sub(r"\s+", " ", " ".join(normalized_parts)).strip()


def _classify_readme_section(heading: str, body_text: str) -> str:
    """按标题优先、正文修正的方式对 README 章节分类。"""

    heading_lower = heading.lower()
    body_lower = body_text.lower()
    best_type = "project_summary"
    best_score = 0.0

    for section_type in _SECTION_TYPES:
        keyword_config = _SECTION_TYPE_KEYWORDS[section_type]
        score = 0.0
        if any(keyword in heading_lower for keyword in keyword_config["title"]):
            score += 0.60
        unique_body_hits = {keyword for keyword in keyword_config["body"] if keyword in body_lower}
        score += min(0.36, 0.12 * len(unique_body_hits))
        if section_type == "module_structure":
            score += _structure_signal_bonus(body_lower)
        if score > best_score:
            best_score = score
            best_type = section_type

    if best_score < 0.25:
        return "project_summary"
    return best_type


def _structure_signal_bonus(text_lower: str) -> float:
    """检测结构类章节的额外结构信号。"""

    hits = sum(1 for keyword in _MODULE_HINT_TERMS if keyword in text_lower)
    if hits >= 3:
        return 0.12
    if hits >= 2:
        return 0.08
    if hits == 1:
        return 0.04
    return 0.0


def _build_signal_terms(values: list[str]) -> list[str]:
    """构建 focus / missing signal 的匹配词。"""

    terms: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            continue
        terms.append(stripped.lower())
        terms.extend(_extract_terms(stripped))
        terms.extend(_expand_query_term(stripped))
    return _unique_list(terms)


def _build_preferred_type_sets(
    *,
    question_text: str,
    focus_topics: list[str],
    missing_signals: list[str],
) -> tuple[set[str], set[str]]:
    """根据当前追问主题生成主偏好和次偏好章节类型。"""

    combined_text = "\n".join([question_text, *focus_topics, *missing_signals]).lower()
    primary: list[str] = []
    secondary: list[str] = []

    if any(term in combined_text for term in ("模块", "架构", "结构", "目录", "architecture", "structure")):
        primary.append("module_structure")
        secondary.append("implementation_anchor")
    if any(term in combined_text for term in ("技术栈", "选型", "依赖", "tech", "stack", "dependency")):
        primary.append("tech_stack")
        secondary.append("implementation_anchor")
    if any(term in combined_text for term in ("维护", "更新", "迭代", "roadmap", "release", "长期")):
        primary.append("maintenance_signal")
    if any(term in combined_text for term in ("实现", "流程", "部署", "pipeline", "workflow", "deployment", "usage")):
        primary.append("implementation_anchor")
        secondary.append("module_structure")

    return set(_unique_list(primary)), set(_unique_list(secondary))


def _collect_matched_terms(
    text: str,
    *,
    missing_terms: list[str],
    focus_terms: list[str],
    question_terms: list[str],
) -> list[str]:
    """收集章节中真正命中的展示词。"""

    haystack = text.lower()
    matched: list[str] = []
    for term in [*missing_terms, *focus_terms, *question_terms]:
        normalized = term.strip().lower()
        if normalized and normalized in haystack:
            matched.append(term)
    return _unique_list(matched)


def _score_readme_section(
    *,
    heading: str,
    body_text: str,
    section_type: str,
    level: int,
    missing_terms: list[str],
    focus_terms: list[str],
    question_terms: list[str],
    preferred_types: tuple[set[str], set[str]],
    matched_terms: list[str],
) -> float:
    """基于当前追问为 README 章节打分。"""

    haystack = body_text.lower()
    query_match_score = 0.0
    for term in missing_terms:
        if term and term.lower() in haystack:
            query_match_score += 0.18
    for term in focus_terms:
        if term and term.lower() in haystack:
            query_match_score += 0.12
    for term in question_terms:
        if term and term.lower() in haystack:
            query_match_score += 0.06

    primary_types, secondary_types = preferred_types
    type_match_bonus = 0.0
    if section_type in primary_types:
        type_match_bonus = 0.20
    elif section_type in secondary_types:
        type_match_bonus = 0.10

    heading_lower = heading.lower()
    heading_bonus = 0.10 if any(
        keyword in heading_lower
        for keyword in _SECTION_TYPE_KEYWORDS.get(section_type, {}).get("title", ())
    ) else 0.0
    info_density_bonus = _compute_info_density_bonus(body_text)
    noise_penalty = _compute_noise_penalty(body_text, matched_terms)
    base_score = 0.28 if level == 0 else 0.35
    return base_score + query_match_score + type_match_bonus + heading_bonus + info_density_bonus - noise_penalty


def _compute_info_density_bonus(text: str) -> float:
    """估算章节的信息密度。"""

    lowered = text.lower()
    signals = 0
    if len(re.findall(r"^[-*+]\s+", text, flags=re.MULTILINE)) >= 2:
        signals += 1
    if sum(1 for keyword in _TECH_HINT_TERMS if keyword in lowered) >= 3:
        signals += 1
    if sum(1 for keyword in _MODULE_HINT_TERMS if keyword in lowered) >= 3:
        signals += 1
    if re.search(r"\b\d+\.\d+(?:\.\d+)?\b", text):
        signals += 1
    if any(symbol in text for symbol in ("、", "/", "->", "=>", ":")):
        signals += 1
    if signals >= 4:
        return 0.12
    if signals >= 3:
        return 0.09
    if signals >= 2:
        return 0.06
    if signals == 1:
        return 0.03
    return 0.0


def _compute_noise_penalty(text: str, matched_terms: list[str]) -> float:
    """惩罚口号化、过短或噪音较多的章节。"""

    normalized = _normalize_text(text)
    lowered = normalized.lower()
    penalty = 0.0
    if len(normalized) < 24:
        penalty += 0.10
    if len(normalized) < 40:
        penalty += 0.06
    if any(term in lowered for term in _README_NOISE_TERMS):
        penalty += 0.08
    if len(matched_terms) == 0 and len(normalized) < 90:
        penalty += 0.06
    return min(0.20, penalty)


def _select_top_readme_sections(sections: list[GitHubReadmeSection]) -> list[GitHubReadmeSection]:
    """选择最适合当前问题的少量 README 章节。"""

    selected: list[GitHubReadmeSection] = []
    for section in sorted(sections, key=lambda item: (-item.relevance_score, item.level, item.heading)):
        if section.relevance_score < 0.45:
            continue
        selected.append(section)
        if len(selected) >= 3:
            break
    return selected


async def _load_key_file_summaries(
    *,
    owner: str,
    repo: str,
    root_entries: list[GitHubRepoTreeEntry],
) -> list[GitHubRepoFileSummary]:
    """按优先级读取关键文件摘要。"""

    existing_paths = {entry.path for entry in root_entries if entry.type == "file"}
    selected_paths = [path for path in _KEY_FILE_PRIORITY if path in existing_paths][:2]
    summaries: list[GitHubRepoFileSummary] = []
    for path in selected_paths:
        try:
            payload = await _invoke_github_get_file_contents(owner=owner, repo=repo, path=path)
        except Exception:
            continue
        content = _extract_primary_text(payload)
        if not content.strip():
            continue
        summaries.append(
            GitHubRepoFileSummary(
                path=path,
                summary=_summarize_key_file(path, content),
                file_role=_infer_file_role(path),
            )
        )
    return summaries


def _parse_directory_entries(payload: Any) -> list[GitHubRepoTreeEntry]:
    """把 GitHub 目录 JSON 过滤为轻量结构。"""

    raw_text = _extract_primary_text(payload)
    if not raw_text.strip():
        return []
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    entries: list[GitHubRepoTreeEntry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entry_type = str(item.get("type", "")).strip()
        name = str(item.get("name", "")).strip()
        path = str(item.get("path", "")).strip()
        if not entry_type or not name or not path:
            continue
        size_value = item.get("size")
        entries.append(
            GitHubRepoTreeEntry(
                type=entry_type,
                name=name,
                path=path,
                size=int(size_value) if isinstance(size_value, int) else None,
            )
        )
    return entries


def _extract_primary_text(payload: Any) -> str:
    """从 MCP 结果中提取主要文本内容。"""

    if isinstance(payload, str):
        return payload

    text_parts: list[str] = []
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

    meaningful_parts = [
        part
        for part in text_parts
        if not any(part.lower().startswith(prefix) for prefix in _SUCCESS_TEXT_PREFIXES)
    ]
    if meaningful_parts:
        return max(meaningful_parts, key=len)
    return max(text_parts, key=len)


def _summarize_key_file(path: str, content: str) -> str:
    """对关键文件内容做轻量摘要。"""

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    joined = " ".join(lines[:12])
    return joined[:400].strip()


def _infer_file_role(path: str) -> str:
    """根据文件名推断关键文件角色。"""

    lowered = path.lower()
    if lowered in {"package.json", "pyproject.toml", "requirements.txt", "pom.xml"}:
        return "tech_stack"
    return "implementation_anchor"


def _truncate_text(text: str, max_length: int) -> str:
    """压缩文本长度。"""

    normalized = _normalize_text(text)
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + "..."


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


async def _invoke_github_get_file_contents(*, owner: str, repo: str, path: str, ref: str = "") -> Any:
    """通过 GitHub MCP 调用 get_file_contents。"""

    tool_args: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "path": path,
    }
    if ref.strip():
        tool_args["ref"] = ref.strip()
    return await invoke_github_mcp_tool("get_file_contents", tool_args)


async def invoke_github_mcp_tool(tool_name: str, tool_args: dict[str, Any]) -> Any:
    """通过 GitHub MCP 调用指定工具。"""

    client = await get_mcp_client_with_retry()
    tools = await client.get_tools()
    target_tool = next((tool for tool in tools if getattr(tool, "name", "") == tool_name), None)
    if target_tool is None:
        raise RuntimeError(f"github {tool_name} tool not found")
    if hasattr(target_tool, "ainvoke"):
        return await target_tool.ainvoke(tool_args)
    if hasattr(target_tool, "arun"):
        return await target_tool.arun(tool_args)
    raise RuntimeError(f"github {tool_name} tool does not support async invocation")


__all__ = [
    "GitHubRepoBindingResult",
    "GitHubReadmeSection",
    "GitHubRepoContextToolInput",
    "GitHubRepoContextToolResult",
    "GitHubRepoFileSummary",
    "GitHubRepoTreeEntry",
    "github_repo_context_tool",
    "invoke_github_mcp_tool",
    "resolve_github_repo_binding",
]
