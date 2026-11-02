# 刊例价来源与计算口径

最近更新：2026-10-23，核实 Fast / Ultrafast 的订阅内额度与 credits 消耗倍率，并新增 Ultrafast 统计；基础 API 单价未变，GPT-6.1 Sol 沿用 2026-10-10、GPT-6 Sol / GPT-6 Luna 沿用 2026-09-23，其余报价沿用 2026-09-13 的核实结果。价格文件为项目根目录的 `prices.json`；单位为美元 / 每百万 token，采用公开 API Standard 短上下文价格。历史用量也套用这份当前价格表，因此展示的是可比较的刊例价估算，不能用来核对历史账单或 Codex 订阅扣费。

## 已核实报价

| 模型 | 普通输入 | 缓存读取 | 缓存写入 | 输出 | 来源 |
| --- | ---: | ---: | ---: | ---: | --- |
| GPT-6 Astra | 10 | 1 | 12.5 | 50 | [模型页](https://developers.openai.com/api/docs/models/gpt-6-astra) |
| GPT-6.1 Sol | 2 | 0.1 | 2.5 | 10 | [模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| GPT-6 Sol | 2 | 0.2 | 2.5 | 10 | [模型页](https://developers.openai.com/api/docs/models/gpt-6-sol) |
| GPT-6 Luna | 0.1 | 0.01 | 0.125 | 0.5 | [模型页](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| GPT-5.6 Sol | 4 | 0.4 | 5 | 20 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |
| GPT-5.6 Terra | 2 | 0.2 | 2.5 | 12 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.6-terra) |
| GPT-5.6 Luna | 0.2 | 0.02 | 0.25 | 1.2 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.6-luna) |
| GPT-5.5 | 5 | 0.5 | 按普通输入价，无额外写入费 | 30 | [模型页](https://developers.openai.com/api/docs/models/gpt-5.5) |

GPT-5.6 Sol 当前公开促销价至少持续到 2026-11-21。GPT-6.1 Sol / Astra / GPT-5.6 的四类单价也已用 [API 总定价表](https://developers.openai.com/api/docs/pricing) 交叉核对。

GPT-5.3-Codex-Spark 在 [Codex 定价页](https://learn.chatgpt.com/docs/pricing) 只列为 research preview，没有数字单价；`gpt-reserve` 的同名公开 API 单价未核实。这些模型不写入价格表，保留其 token 统计并显示价格未知，不将未知价当作零，也不套用名称相似的模型价格。`codex-auto-review` 按用户指定的统计范围完全排除，不参与 token、金额和模型明细；此设置不代表其官方收费政策。

## 输入、缓存读取与缓存写入

[官方 Prompt caching 文档](https://developers.openai.com/api/docs/guides/prompt-caching) 的计费示例明确把输入总量拆成三个互斥类别：普通输入、缓存读取、缓存写入。缓存写入价替代该部分 token 的普通输入价，不是重复加收整笔输入费。

```text
I = input_tokens
R = cached_input_tokens
W = cache_write_input_tokens
普通输入 U = I - R - W

美元估算 = (U × input + R × cached + W × cache_write + O × output) / 1,000,000
```

API 原始字段为 `usage.input_tokens_details.cached_tokens` 与 `cache_write_tokens`，本地 Codex 日志使用上面的扁平字段名。GPT-5.5 没有额外写缓存费，因此价格表明确将它的 `cache_write` 设为与普通输入相同的 5 美元。其他模型如果缺少写缓存价且存在写入用量，保持部分已知，不猜测回退价格。缺少旧日志字段与明确记录零值应在数据质量上区分。

本次仅抽取本机 `token_count` 的模型和数值元数据，没有输出对话。2026-09-13 的日志可见写缓存字段，但抽样时数值均为 0；数据采集代理的更大范围扫描也未发现非零值。因此，本机记录能确认字段存在和 `total_tokens = input_tokens + output_tokens` 的表现，不能单靠这些零值样本证明写缓存的包含关系；拆分公式依据上述官方定义。

推理输出是 `output_tokens` 的子集；不可再把 `reasoning_output_tokens` 加进总 token 或重复计价。数据异常导致 `R + W > I` 时应记录异常，不能生成负输入费用。

## 速度模式倍率

2026-10-23 查阅 [官方 Speed 说明](https://learn.chatgpt.com/docs/agent-configuration/speed)，并与 [Codex 定价页](https://learn.chatgpt.com/docs/pricing) 交叉核对：

| 速度模式 | 订阅内额度消耗 | 购买 credits / 企业按量付费 |
| --- | ---: | ---: |
| Fast / Priority | 2.5× | 2× |
| Ultrafast（GPT-6 Astra / GPT-6.1 Sol） | 8× | 6× |

本便签沿用原 Fast 的订阅内额度口径，将基础模型 API Standard 短上下文单价乘对应倍率作美元展示估算：Fast / Priority 为 2.5 倍，Ultrafast 为 8 倍。普通输入、缓存读取、缓存写入、输出均使用对应倍率，Token 数不乘倍率。公式为前述标准美元估算再乘速度倍率。这是可比较的展示口径，不是官方 API 速度价格或订阅实际账单；API key 使用独立 API 计价，ChatGPT 倍率不适用。

日志没有每次用量的额度支付来源，无法判断来自订阅内额度还是购买 credits，所以不会自动按 6 倍切换 Ultrafast。账号额度仍以官方接口快照为准，不把剩余额度百分比换算成美元。

`service_tier = ultrafast` 单列为 `模型 [ULTRAFAST]`，Fast / Priority 保留 `模型 [FAST]`，其余保留普通模型名；自然与最近范围的汇总、曲线、模型明细及 CLI 使用同一计价逻辑。同一日志的后续轮次或部分设置省略速度时，继承最后明确的线程速度，显式 `default` 或 `null` 才清回普通。修复前的读取器错误地在后续 `turn_context` 缺少字段时清空速度，会将部分 Ultrafast 误归普通；解析器升级到版本 2，启动后自动重新读取原始日志来修正旧缓存，无需手动重建。始终未记录速度的日志仍无法推断，不按相似模型或其他线程猜测。

## 未自动应用的收费变化

- 长上下文：GPT-6 / GPT-5.6 单次请求输入超过 272,000 token 时，输入、缓存读取及缓存写入价格为短上下文的 2 倍，输出为 1.5 倍。GPT-5.5 模型页另写有整段 session 的长上下文规则。当前便签统一展示标准短上下文等价估算，不自动加这些倍率。[Astra 模型页](https://developers.openai.com/api/docs/models/gpt-6-astra)、[Sol 模型页](https://developers.openai.com/api/docs/models/gpt-6-sol)、[Luna 模型页](https://developers.openai.com/api/docs/models/gpt-6-luna)、[GPT-5.5 模型页](https://developers.openai.com/api/docs/models/gpt-5.5)
- 不含地区处理、工具调用、图像、语音等单独费用。[API 定价表](https://developers.openai.com/api/docs/pricing)
