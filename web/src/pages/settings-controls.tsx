import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { SecretControl } from "./settings-secret"
import {
  isNumericScope,
  isSecret,
  type SettingItem,
  type SettingSpec,
  valueText,
} from "./settings-specs"

/**
 * 一项配置的控件：按数据的语义选，而不是一律给文本框。
 *
 * - 有界偏好（播放倍速）→ `Slider` + 数字输入：拖得了，也填得准；
 * - 精确数值（超时、并发、间隔）→ 数字输入，带 min/max/step 与单位；
 * - 小枚举（日志级别）→ `Select`（单选，不需要长列表与搜索）；
 * - 持续开关 → `Switch`（表示"长期生效的二元状态"，不是一次性勾选）；
 * - 密钥 → masked 输入 + 替换：服务端本来就只回显"是否已设置"；
 * - 其余 → 文本 `Input`。
 */
export function SettingControl({
  id,
  settingKey,
  item,
  spec,
  canWrite,
  draft,
  sent,
  onDraft,
  onCommitDraft,
  onCommitValue,
}: {
  id: string
  settingKey: string
  item: SettingItem
  spec?: SettingSpec
  canWrite: boolean
  /** 正在编辑的原始文本；null 表示还没改过，显示服务端值。 */
  draft: string | null
  /** 上行请求在飞：期间禁用控件，避免连点写两次。 */
  sent: boolean
  onDraft: (text: string) => void
  onCommitDraft: () => void
  onCommitValue: (next: unknown) => void
}) {
  const disabled = !canWrite || sent
  const text = draft ?? valueText(item.value)

  if (isSecret(settingKey)) {
    return (
      <SecretControl
        id={id}
        disabled={disabled}
        configured={valueText(item.value) !== ""}
        onReplace={onCommitValue}
      />
    )
  }

  if (spec?.options) {
    return (
      <Select
        value={valueText(item.value)}
        disabled={disabled}
        onValueChange={(next) => {
          if (typeof next === "string") onCommitValue(next)
        }}
      >
        <SelectTrigger id={id} className="w-full sm:w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {spec.options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  if (item.type === "bool") {
    return (
      <Switch
        id={id}
        checked={item.value === true || item.value === "true"}
        disabled={disabled}
        onCheckedChange={(checked) => onCommitValue(checked)}
      />
    )
  }

  if (isNumericScope(item, spec)) {
    const current = Number(text)
    return (
      <div className="flex items-center gap-3">
        {spec?.slider ? (
          <Slider
            className="max-w-56 flex-1"
            aria-labelledby={`${id}-label`}
            value={[Number.isFinite(current) ? current : (spec.min ?? 0)]}
            min={spec.min}
            max={spec.max}
            step={spec.step}
            disabled={disabled}
            onValueChange={(next) =>
              onDraft(String(Array.isArray(next) ? Number(next[0]) : Number(next)))
            }
            onValueCommitted={(next) =>
              onCommitValue(Array.isArray(next) ? Number(next[0]) : Number(next))
            }
          />
        ) : null}
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          className="w-24"
          value={text}
          min={spec?.min}
          max={spec?.max}
          step={spec?.step}
          disabled={disabled}
          onChange={(event) => onDraft(event.target.value)}
          onBlur={onCommitDraft}
          onKeyDown={(event) => {
            if (event.key === "Enter") onCommitDraft()
          }}
        />
        {spec?.unit ? <span className="text-xs text-muted-foreground">{spec.unit}</span> : null}
      </div>
    )
  }

  return (
    <Input
      id={id}
      type={spec?.url ? "url" : "text"}
      value={text}
      disabled={disabled}
      onChange={(event) => onDraft(event.target.value)}
      onBlur={onCommitDraft}
      onKeyDown={(event) => {
        if (event.key === "Enter") onCommitDraft()
      }}
    />
  )
}
