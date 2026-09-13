# 开源工具接入说明

## 聚合方式

本仓库采用“注册表 + 统一记录 + 交付前检查”，不把所有上游源码复制到 Skill 中。复制源码会造成许可证混用、版本失控、重型依赖和安全更新滞后；注册表用固定版本记录可复现依据，适配器只需输出统一字段。

`open_source_stack.json`中的状态含义：

- `baseline`：职业标准基线，可用于归一化，但必须披露版本。
- `optional_adapter`：已实现的可选接口，需单独准备依赖或服务。
- `experimental`：可做技能匹配建议，结果必须人工复核。
- `pattern_only`：只借鉴交互或分析方法，不依赖其分数和结论。
- `discovery_only`：只能发现线索，不能进入已确认可投主清单。
- `evaluation_only`：只用于离线评估分类或实体链接质量。

所有组件的代码状态见`integration_state`，当前均为`adapter_implemented`，对应适配文件、契约测试和验证记录均在注册表中。`default_enabled=false`保留为按需启用的安全默认值，不再表示没有实现。安装、实际调用、上游版本核验、效果评估是不同状态，见[runtime-verification.json](runtime-verification.json)。调用参数、依赖与完整CLI见[运行手册](../references/runtime-guide.md)。

## 统一岗位资料记录

所有解析器、爬取器和 Agent 最终都要转换成下面的记录。字段缺失不能靠模型猜测。

```json
{
  "corpus_id": "stable-id",
  "company_key": "normalized-employer",
  "company": "雇主显示名",
  "title": "JD原始职位名",
  "employment_mode": "campus_full_time",
  "geography": "杭州/广州/深圳",
  "locations": ["杭州", "广州", "深圳"],
  "source_tier": "employer_official",
  "jd_url": "https://example.org/jobs/123",
  "captured_at": "2026-09-13T10:00:00+08:00",
  "status": "open",
  "source_text": "JD完整正文，仅在本地受限研究记录保存",
  "source_sha256": "填写source_text的UTF-8 SHA256",
  "full_jd": true,
  "comparable": true,
  "responsibilities": ["保留JD原词的职责摘要"],
  "requirements": [
    {
      "text": "保留JD原词的条件",
      "source_excerpt": "须能在source_text逐字定位的条件摘录",
      "required": true,
      "normalized_skills": ["financial analysis"],
      "taxonomy_refs": ["ESCO URI或O*NET编号"]
    }
  ],
  "excluded_reason": ""
}
```

岗位资料文件顶层包含：

```json
{
  "method_version": "3.0",
  "employment_mode": "campus_full_time",
  "geography": "杭州/广州/深圳",
  "locations": ["杭州", "广州", "深圳"],
  "collected_at": "2026-09-13T12:00:00+08:00",
  "candidate_evidence": [
    {"id":"E1","source_id":"resume","locator":"第1页项目经历第1条","text":"简历中的经历原文","state":"completed","confirmed":true}
  ],
  "candidate_pool_count": 48,
  "taxonomies": [
    {
      "name": "ESCO",
      "version": "1.2.1",
      "use": "技能归一化",
      "url": "https://esco.ec.europa.eu/en/use-esco"
    }
  ],
  "records": []
}
```

示例只说明字段，不是可投数据。运行`build_evidence_basis.py`后得到`evidence_basis`和`corpus_summary`。岗位报告JSON同时嵌入完整`corpus`，校验器从中重算统计。空JD、过期采集、超出城市范围、重复来源以及断裂的材料引用会报错。详细约定见[validation.md](../references/validation.md)。

## 推荐流水线

1. **本地解析**：PDF/DOCX优先用Docling或现有文档工具；保留页码、段落位置和原文件，不上传私人材料。
2. **岗位发现**：先查本地库、官网、官方ATS；Crawl4AI只抽取允许访问的公开页。JobSpy只补充境外线索。
3. **岗位资料归一化**：保留中文JD原词；O*NET/ESCO添加标准引用，不覆盖原词。Tabiya或ESCO Skill Extractor的结果标为模型候选。
4. **补充经历说明**：借鉴Compass逐项向用户确认经历；未确认的技能留在待确认项。
5. **差距与改写**：借鉴Resume Matcher呈现关键词和差距；每条改写必须关联经历编号。
6. **离线评估**：需要比较分类器时用MELO或TGRE；评估集与中国岗位不一致时明确限制。
7. **复核与交付**：运行岗位资料汇总、集成注册表检查和`validate_jobs.py`，再逐页检查Word。

## Agent 输出协议

SourceScout提取来源事实；EvidenceMapper对照岗位要求与已有经历材料；Auditor通过独立模型调用检查遗漏与失真。机器审核通过只产生`proposed_selection=true`，仍保持`selected=false`。只有人工核对来源身份、开放状态、完整JD、资格和改写忠实度，并提交匹配原文校验码的review文件后，`finalize`才允许计入已确认数。Agent文字不能直接成为JD原文，也不能覆盖原始摘录。

## 安全与升级

- 不自动执行`pip install`、Docker或第三方脚本；启用前查看固定提交、依赖和安全公告。
- Crawl4AI曾发布多次服务端安全修复；部署抓取服务时使用当前已审查版本，禁用不需要的外部入口。
- API Key只从环境变量读取，不写入仓库、报告或岗位资料。
- 更新`pin`或许可证后，同步修改`verified_at`和`THIRD_PARTY.md`，再运行`validate_integrations.py`。
