'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useObjectContracts, useLinkObjectContract, useUnlinkObjectContract } from '@/shared/api/objects'
import { useContracts, CONTRACT_STATUS_LABELS } from '@/shared/api/contracts'
import { useCan } from '@/shared/auth/hooks'
import { Button } from '@/shared/ui/button'

function LinkContractModal({
  objectId,
  onClose,
}: {
  objectId: string
  onClose: () => void
}) {
  const link = useLinkObjectContract(objectId)
  const [contractId, setContractId] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await link.mutateAsync(contractId.trim())
      onClose()
    } catch (err) {
      alert((err as Error).message)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-sm rounded-lg bg-background p-6 shadow-xl">
        <h2 className="mb-4 text-base font-semibold">Привязать договор</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">UUID договора</label>
            <input
              required
              value={contractId}
              onChange={(e) => setContractId(e.target.value)}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>Отмена</Button>
            <Button type="submit" size="sm" disabled={link.isPending}>
              {link.isPending ? 'Привязка...' : 'Привязать'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ContractRow({ contractId, objectId }: { contractId: string; objectId: string }) {
  const { data } = useContracts({ limit: 1 })
  const unlink = useUnlinkObjectContract(objectId)
  const canWrite = useCan('objects.write')

  return (
    <tr className="border-b border-border hover:bg-muted/30">
      <td className="px-4 py-3 font-mono text-xs">{contractId}</td>
      <td className="px-4 py-3 text-right">
        <div className="flex justify-end gap-2">
          <Link href={`/dashboard/contracts/${contractId}`}>
            <Button variant="ghost" size="sm">→</Button>
          </Link>
          {canWrite && (
            <Button
              variant="ghost"
              size="sm"
              disabled={unlink.isPending}
              onClick={() => unlink.mutate(contractId)}
              className="text-destructive hover:text-destructive"
            >
              Отвязать
            </Button>
          )}
        </div>
      </td>
    </tr>
  )
}

export function ContractsTab({ objectId }: { objectId: string }) {
  const { data: links, isLoading } = useObjectContracts(objectId)
  const canWrite = useCan('objects.write')
  const [showLink, setShowLink] = useState(false)

  if (isLoading) {
    return <div className="py-8 text-center text-sm text-muted-foreground">Загрузка...</div>
  }

  return (
    <div className="mt-2">
      {canWrite && (
        <div className="mb-3 flex justify-end">
          <Button size="sm" variant="outline" onClick={() => setShowLink(true)}>
            + Привязать договор
          </Button>
        </div>
      )}

      {!links || links.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          Договоры не привязаны
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">UUID договора</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {links.map((lnk) => (
                <ContractRow key={lnk.contract_id} contractId={lnk.contract_id} objectId={objectId} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showLink && <LinkContractModal objectId={objectId} onClose={() => setShowLink(false)} />}
    </div>
  )
}
