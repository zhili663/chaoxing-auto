import { PlusIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import type { AccountGroupItem } from "@/lib/hooks"

import { AccountCreateForm } from "./account-create-form"

/**
 * 添加账号的二级 Dialog：大 Dialog 承载一段有界编辑，保留列表上下文。
 *
 * 内部三段（身份 / 凭据 / 备注与分组）见 {@link AccountCreateForm}；提交即入库、密码不回显。
 */
export function AccountCreateDialog({
  groups,
  onCreated,
}: {
  groups: AccountGroupItem[]
  onCreated: () => void
}) {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState(0)

  function close() {
    setOpen(false)
    // 重新挂载表单：清掉上一轮的密码与错误，避免残留
    setKey((value) => value + 1)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setOpen(true)
        else close()
      }}
    >
      <DialogTrigger render={<Button className="h-11 sm:h-9" />}>
        <PlusIcon data-icon="inline-start" />
        添加账号
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>添加超星账号</DialogTitle>
          <DialogDescription>
            密码经 Fernet 加密入库（可逆 —— 代登录必须取回明文），任何响应都不回显。
          </DialogDescription>
        </DialogHeader>
        <AccountCreateForm key={key} groups={groups} onCreated={onCreated} onClose={close} />
      </DialogContent>
    </Dialog>
  )
}
