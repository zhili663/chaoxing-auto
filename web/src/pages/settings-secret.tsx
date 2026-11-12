import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"

/**
 * 密钥输入：**只回显"是否已设置"**，替换必须重新输入完整新值。
 *
 * 服务端返回的就是"（已设置）"这个哨兵，所以这里不提供"查看原值"的动作 ——
 * 能看见的密钥迟早会被复制到聊天记录里。
 */
export function SecretControl({
  id,
  configured,
  disabled,
  onReplace,
}: {
  id: string
  configured: boolean
  disabled: boolean
  onReplace: (value: string) => void
}) {
  const [replacing, setReplacing] = useState(false)
  const [value, setValue] = useState("")

  if (!replacing) {
    return (
      <InputGroup>
        <InputGroupInput
          id={id}
          type="password"
          value=""
          readOnly
          disabled={disabled}
          placeholder={configured ? "已设置（不回显）" : "未设置"}
        />
        <InputGroupAddon align="inline-end">
          <InputGroupButton disabled={disabled} onClick={() => setReplacing(true)}>
            替换
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <Input
        id={id}
        type="password"
        autoComplete="new-password"
        placeholder="输入新的值"
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
      />
      <Button
        size="sm"
        disabled={disabled || value === ""}
        onClick={() => {
          onReplace(value)
          setValue("")
          setReplacing(false)
        }}
      >
        保存
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          setValue("")
          setReplacing(false)
        }}
      >
        取消
      </Button>
    </div>
  )
}
