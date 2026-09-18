# Companion 预设设计

## 已落地：Data 是官方预设的唯一内容来源

`eidolon_data/resources/companion_presets/` 中每个 JSON 是一份完整的
`PersonaPreset`，`catalog.json` 的 `presets` 决定展示顺序。服务使用
`importlib.resources` 加载安装包内资源，不依赖进程工作目录或 SDK 默认人格。
加载时检查完整字段、唯一 ID、文件清单、版本和示例；错误直接暴露，不退回另一套模板。

SDK 只保留跨进程模型 `PersonaPreset` / `PersonaPresetCatalog`，不再提供目录工厂或
任何官方模板内容。Admin 继续转发 Data 接口，Mobile/Web 读取同一份目录。
修改模板内容必须提升 `revision`。创建沿用完整人格快照写入流程，更新模板不会
覆盖已创建伙伴。

来源模板会记进 provenance：`source_preset_id` 与 `source_preset_revision`。
**它只是记录。** 人格在创建时整份写定，所以提升模板 `revision` 不会回溯已创建的
伙伴；没有任何读取路径会去解析这两个字段来决定行为，伙伴此后的自进化与模板无关。
声明由客户端给出——只有它持有草稿、知道用户有没有改过；Data 只记录，不比对推断。

### 怎么读这份 provenance

`origin` 一个词不够用，必须和 `source_preset_id` 一起读。四种形状：

| origin | source_preset_id | 含义 |
| --- | --- | --- |
| `template` | 有 | 原封不动取用了这份官方模板 |
| `template` | 无 | SDK 内置默认人格（onboarding 建的第一个伙伴，或创建时没带 persona） |
| `owner_authored` | 无 | 从零写的 |
| `owner_authored` | 有 | 从这份模板起步，之后被改写过 |

第四种是**正常且有意**的：`source_preset_id` 说的是 v1 从哪来，`origin` 说的是当前
这一版怎么来的。编辑、改名、恢复都会把 `source_preset_id` 继续带给后代版本，因为
"它从哪开始"不会因为后来被改过就不再为真。

genome 行的 `source_json.source_type` 画同样三条线（`companion_preset` /
`owner_authored` / `companion_provision`），不是两条。`persona_service` 会从
`source_type` 反推 `origin`，两列若能各说各话，同一件事就成了两件事。

### Data 只拒绝不可能为真的声明

不比对正文去猜"这份人格是不是那个模板"——那种推断在悄悄出错之前一直是对的。
能拒的只有当面就假的声明，两条：没有 `source_preset_id` 的 `source_preset_revision`
（一个不属于任何模板的版本号），以及不带 persona 的模板声明（那会把主机自己造的
默认人格标成用户选的模板）。有 id 无 revision 是**残缺但为真**，因此放行——
"哪份模板被选了"这个问题，光靠 id 就能回答。

四份模板为原创内容，借鉴下面的产品机制，不复制竞品角色或提示词。

| ID | 相处方式 | 用户说“今天有点累”时的示例 |
| --- | --- | --- |
| gentle | 温柔倾听：先倾听，不急着开导 | 辛苦了，先歇一会儿。我在。 |
| direct | 清醒直率：坦诚、有判断，也能接住情绪 | 今天消耗不小，先缓口气。 |
| playful | 轻松有趣：轻巧幽默，知道何时认真 | 今天电量见底了吧，先歇会儿。 |
| curious | 好奇探索：一次分享一个观察，不盘问 | 那今天先不探索了，歇歇。 |

每份模板保存默认名字、简短简介、人物身份、完整人格、短句示例和对话偏好
（`preferences`：详略、建议时机、追问习惯）。选中模板即可直接创建，名字和
详细设定都是可选调整。

语音共同要求写在 `persona.modality_notes.voice`：日常 1–3 个短句，一个重点；
不强行追问、不念 Markdown/表情/动作旁白；用户要求详细解释或故事时自然展开。
这些是生成指引，不是字符串硬裁剪，也和 TTS 音色无关。模板同时给出行为指引及
少量对话示例，不能仅靠性格标签保证差异。上述示例为编辑样例，不是模型实测输出。

## 音色不在本阶段

伙伴不携带自己的音色。说话的声音由这台主机的 Channel 配置决定，对所有伙伴
一致，创建和编辑流程里都没有音色选择或试听。这是本阶段刻意收窄的范围：让
“选一个模板就能开始”保持简单。将来若要让伙伴各有各的声音，需要重新设计
可用性协商和会话内绑定失败时的行为，不要从这里的任何字段推断那份设计。

## 竞品依据（2026-09-17 查阅）

- [Replika — What is Replika?](https://help.replika.com/hc/en-us/articles/115001070951-What-is-Replika)：强调倾听、陪伴，允许选择关系方向。
  启发是先让用户理解“怎么相处”，Eidolon 不强制在创建时选择恋爱或亲密关系。
- [Nomi — Getting Started](https://nomi.ai/nomi-knowledge/nomi-101-a-beginners-guide-to-getting-started-with-your-ai-companion/)：基础性格和兴趣作为交流起点，Shared Notes 可进一步塑造人物。
  启发是预设应完整可用，进一步描述应可选。该入门文发表于 2024 年，不能据此认定当前所有 UI 细节。

## Mobile 创建体验

首屏直接选模板创建，名字和详细设定都为可选调整；自定义入口继续保留。
每个模板和自定义分别保留草稿，创建请求发送期间禁止重复提交；结果不确定时
使用相同操作 ID 和完整请求重试。创建成功可直接进入这个伙伴的对话，也可稍后再聊。
