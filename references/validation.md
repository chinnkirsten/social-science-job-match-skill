# 交付检查数据约定（方法3.0）

新建或重新生成报告执行本约定；旧报告未经重新核验不能宣称通过。所有数据均保存在候选人的本地任务目录，不进入Skill仓库。

## 单一数据来源

报告JSON顶层包含target_companies（默认20）、employment_mode、corpus、evidence_basis和jobs。corpus是完整岗位资料对象，与build_evidence_basis.py的输入相同。evidence_basis必须逐字段等于从corpus重算的结果；不能手填另一份汇总数字。

corpus必需字段：

- method_version：本轮填写3.0。
- employment_mode：internship / campus_full_time / experienced_full_time。
- geography：地区显示文字；locations：规范化城市列表，例如杭州、广州、深圳。远程须明确确认范围并使用一致标签。
- collected_at：本批收集截至时间，含时区，不得晚于当前时间或早于7天。不能以新批次时间刷新旧记录。
- candidate_pool_count：搜索池数量，至少等于records数；保留查询日志支持该数值。
- candidate_evidence：经历记录。
- taxonomies：实际使用的分类库，每项含name、version、use、url；未使用填空列表。
- records：岗位原始记录。每条含corpus_id、company_key、company、title、employment_mode、geography、locations、source_tier、jd_url、captured_at、status、full_jd、comparable、responsibilities、requirements。排除/不完整项加excluded_reason。

可比完整JD必须处于open、城市与本轮范围有交集、实际采集时间在7天内且不晚于collected_at，并具有非空职责和条件。历史JD仍可留在records中，设comparable=false并写原因。招聘模式不同的岗位资料另存，不混进同批统计。

每条完整可比JD保存source_text（本地原始正文）与source_sha256（正文UTF-8字节的SHA256）；requirements每项含text、required布尔值、source_excerpt、normalized_skills列表，source_excerpt须能在正文逐字找到。摘录与摘要分开，不能用摘要冒充原文。taxonomy_refs可记录实际核验的标准技能引用。

去重检查corpus_id、去已知追踪参数后的URL、正文校验码和company_key+employer_job_id（若有）。URL保留岗位查询参数与单页应用片段路由；疑似重复必须回到招聘页面确认后整理，不能简单换ID绕过。

## 经历记录

每项含id、source_id（匿名本地素材编号）、locator（页码/段落或用户补充位置）、text（简历或用户已说明的事实）、state和confirmed布尔值。

state为completed / ongoing / planned / unconfirmed。confirmed代表简历明确自述或用户明确确认，不代表第三方独立认证。保留经历的完成与确认状态；推断不能标confirmed。技能事实与计划学习分开登记。

candidate_evidence_count由经历记录条数计算。改写、资格条件和映射引用的evidence_refs均须存在；不能用编号存在证明语义正确，仍须逐条核对改写是否超出简历或用户已说明的事实。

## 报告岗位记录

- id、corpus_id、company_key、company、title、employment_mode、entity_type：每条主清单关联真实岗位资料记录。company_key、title、模式、来源等级与JD URL须与该记录一致。
- selected布尔值；status为open/closed/unknown；eligibility为pass/fail/unknown；priority为A/B或空。只允许公司或合伙企业计入20家公司。
- source_tier为employer_official、official_ats、employer_verified_platform、official_repost或aggregator；主清单仅限前三类，且发布主体须人工核验。
- checked_at：交付前24小时内实际核验开放状态的时刻；open_evidence：具体开放依据；apply_url或application_method：有来源的申请路径。可选closes_at须有时区依据，不编造具体截止时刻。
- hard_requirements_reviewed：完整JD必需条件已读源检查。
- requirements：text、required、result（met/unmet/unknown）、jd_evidence（可在岗位资料原文定位的摘录）、candidate_evidence（说明文字）、evidence_refs。met必须关联confirmed且completed/ongoing的经历记录。未知项允许空引用列表并明确未体现。
- mappings：至少3项，每项含requirement、resume_evidence、evidence_refs、gap、action；没有对应经历时引用列表可空，说明缺口。
- rewrites：至少2项，含text、placement、evidence_refs、use_status。ready必须引用已确认且已完成/进行中的事实，并设fidelity_reviewed=true表示已人工对照原文；verify_first和create_first不作为已完成经历。
- 未入选记录保存reason。未知必需条件不计入主清单。

## 统计口径

单批岗位资料仅支持sample_observation；无可比岗位为none。不得按样本数量、来源比例阈值生成market_pattern。跨时段趋势需要独立分析，当前脚本不支持此声明。

市场JD数量、雇主数、来源分布与技能频数从records计算；candidate_evidence_count从经历记录计算。corpus_as_of取可比岗位最早采集时间，防止新批次掩盖旧资料。样本为零可汇总，但不满足正式20家公司报告的交付条件。

## 运行与边界

```sh
python3 scripts/build_evidence_basis.py corpus.json --output evidence-new.json
python3 scripts/validate_jobs.py jobs.json
python3 scripts/validate_integrations.py integrations/open_source_stack.json
python3 -B -m unittest discover -s scripts -p 'test_*.py'
```

汇总输出只写入新文件，拒绝覆盖输入或已有结果。测试可使用--as-of指定带时区时刻；真实交付使用当前时间。退出0仅表示结构检查通过，退出1为数据错误，退出2为文件读写错误。检查不联网，也不证明雇主身份、原文真实性或改写语义。

数据通过后使用`run_job_match.py report`或经人工复核的`finalize`生成Word，再按report-standard.md核对内容、无底纹、可点击链接和逐页渲染。`evidence_ok`区分事实结构问题与公司数不足；只有数量不足可以使用阶段报告，不能绕过资料检查。自动生成成功返回`render_pending`，不代表视觉验收或真实求职效果实测。
