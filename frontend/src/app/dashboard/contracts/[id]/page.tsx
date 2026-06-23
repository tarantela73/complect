'use client'

import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useCan } from '@/shared/auth/hooks'
import { useAuthStore } from '@/shared/auth/store'
import { downloadPdf } from '@/shared/api/reports'
import {
  useContract,
  useActivateContract,
  useCloseContract,
  CONTRACT_STATUS_LABELS,
  RESOURCE_TYPE_LABELS,
  type LastPayment,
} from '@/shared/api/contracts'
import { useContragent } from '@/shared/api/contragents'
import {
  useContractAttachments,
  useAttachFile,
  useDetachFile,
  type Attachment,
} from '@/shared/api/attachments'
import { useContractEvents, type ContractEventLogEntry } from '@/shared/api/contracts'
import { useFiles, FILE_STATUS_LABELS, FILE_STATUS_COLORS, formatFileSize } from '@/shared/api/files'
import { Button } from '@/shared/ui/button'
import { ObjectsSection } from './components/ObjectsSection'

const STATUS_COLORS: Record<string, string> = {
  DRAFT: 'bg-muted text-muted-foreground',
  ACTIVE: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  LEGAL_HOLD: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  CLOSED: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
}

function formatAmount(kopecks: number) {
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(kopecks / 100)
}

function ContragentLink({ contragentId }: { contragentId: string }) {
  const { data, isLoading } = useContragent(contragentId)
  if (isLoading) return <span className="inline-block h-4 w-32 animate-pulse rounded bg-muted" />
  if (!data) return <span className="text-muted-foreground">—</span>
  return (
    <Link
      href={`/dashboard/contragents/${contragentId}`}
      className="text-primary hover:underline"
    >
      {data.name}
    </Link>
  )
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className={`text-sm flex-1 ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  )
}

const MUTABLE_CONTRACT_STATUSES = new Set(['DRAFT', 'ACTIVE'])

const ATTACHMENT_TYPE_LABELS: Record<string, string> = {
  CONTRACT_SIGNED: 'Подписанный договор',
  ACT_COMPLETED: 'Акт выполненных работ',
  ESTIMATE: 'Смета',
  INVOICE: 'Счёт',
  OTHER: 'Прочее',
}

const ATTACHMENT_TYPE_COLORS: Record<string, string> = {
  CONTRACT_SIGNED: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  ACT_COMPLETED: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  ESTIMATE: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
  INVOICE: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400',
  OTHER: 'bg-muted text-muted-foreground',
}

const EVENT_COLORS: Record<string, string> = {
  'contracts.ContractCreated': 'bg-blue-500',
  'contracts.ContractActivated': 'bg-green-500',
  'contracts.ContractClosed': 'bg-slate-400',
}

function EventTimeline({ contractId }: { contractId: string }) {
  const { data, isLoading } = useContractEvents(contractId)

  if (isLoading) return null
  if (!data || data.count === 0) return null

  return (
    <div className="mt-6">
      <h2 className="mb-3 text-sm font-semibold">История</h2>
      <ol className="relative border-l border-border ml-2">
        {data.results.map((entry: ContractEventLogEntry) => (
          <li key={entry.event_id} className="mb-4 ml-4">
            <span
              className={`absolute -left-1.5 mt-1 h-3 w-3 rounded-full border border-background ${EVENT_COLORS[entry.event_type] ?? 'bg-muted-foreground'}`}
            />
            <p className="text-sm font-medium">{entry.label}</p>
            <p className="text-xs text-muted-foreground">
              {new Date(entry.occurred_at).toLocaleString('ru-RU')}
            </p>
          </li>
        ))}
      </ol>
    </div>
  )
}

function AttachmentsSection({
  contractId,
  contractStatus,
  canWrite,
  filesAvailable = 0,
  filesQuarantined = 0,
}: {
  contractId: string
  contractStatus: string
  canWrite: boolean
  filesAvailable?: number
  filesQuarantined?: number
}) {
  const [showPicker, setShowPicker] = useState(false)
  const [attachError, setAttachError] = useState('')
  const [pendingType, setPendingType] = useState<Record<string, string>>({})

  const { data: attachments, isLoading } = useContractAttachments(contractId)
  const { data: files } = useFiles({ status: 'AVAILABLE', limit: 100 })
  const isMutable = MUTABLE_CONTRACT_STATUSES.has(contractStatus)
  const attachMutation = useAttachFile(contractId)
  const detachMutation = useDetachFile(contractId)

  const attachedIds = new Set(attachments?.results.map((a) => a.file_id) ?? [])
  const availableToAttach = files?.results.filter((f) => !attachedIds.has(f.id)) ?? []

  const handleAttach = (fileId: string) => {
    setAttachError('')
    attachMutation.mutate(
      { fileId, attachmentType: pendingType[fileId] ?? 'OTHER' },
      {
        onSuccess: () => {
          setShowPicker(false)
          setPendingType({})
        },
        onError: (e) => setAttachError(e.message),
      },
    )
  }

  const handleDetach = (fileId: string, name: string) => {
    if (!confirm(`Открепить файл «${name}»?`)) return
    detachMutation.mutate(fileId, {
      onError: (e) => alert(e.message),
    })
  }

  return (
    <div className="mt-6">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold">Вложения</h2>
          {filesAvailable > 0 && (
            <span className="text-xs text-muted-foreground">{filesAvailable} доступно</span>
          )}
          {filesQuarantined > 0 && (
            <span className="text-xs text-destructive">{filesQuarantined} на карантине</span>
          )}
        </div>
        {canWrite && isMutable && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => { setShowPicker((v) => !v); setAttachError('') }}
          >
            {showPicker ? 'Отмена' : 'Прикрепить файл'}
          </Button>
        )}
      </div>

      {showPicker && (
        <div className="mb-3 rounded-md border border-border bg-muted/30 p-3">
          {attachError && <p className="mb-2 text-xs text-destructive">{attachError}</p>}
          {availableToAttach.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Нет доступных файлов. Загрузите файл в разделе{' '}
              <Link href="/dashboard/files" className="underline">Файлы</Link>.
            </p>
          ) : (
            <ul className="space-y-2 max-h-56 overflow-y-auto">
              {availableToAttach.map((f) => (
                <li key={f.id} className="flex items-center gap-2 text-sm">
                  <span className="flex-1 truncate">{f.original_name}</span>
                  <span className="text-xs text-muted-foreground shrink-0">{formatFileSize(f.size_bytes)}</span>
                  <select
                    className="rounded border border-border bg-background px-1.5 py-1 text-xs"
                    value={pendingType[f.id] ?? 'OTHER'}
                    onChange={(e) => setPendingType((prev) => ({ ...prev, [f.id]: e.target.value }))}
                  >
                    {Object.entries(ATTACHMENT_TYPE_LABELS).map(([v, l]) => (
                      <option key={v} value={v}>{l}</option>
                    ))}
                  </select>
                  <Button
                    size="sm"
                    variant="outline"
                    loading={attachMutation.isPending}
                    onClick={() => handleAttach(f.id)}
                  >
                    Прикрепить
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {isLoading ? (
        <p className="text-xs text-muted-foreground">Загрузка вложений...</p>
      ) : attachments?.results.length === 0 ? (
        <p className="text-xs text-muted-foreground">Вложений нет</p>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full">
            <tbody>
              {attachments?.results.map((a: Attachment) => (
                <tr key={a.file_id} className="border-b border-border last:border-0 hover:bg-muted/20">
                  <td className="px-3 py-2 text-sm truncate max-w-xs">
                    {a.download_url ? (
                      <a
                        href={a.download_url}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:underline text-foreground"
                      >
                        {a.name}
                      </a>
                    ) : (
                      <span className="text-muted-foreground">{a.name}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {a.attachment_type && (
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${ATTACHMENT_TYPE_COLORS[a.attachment_type] ?? 'bg-muted text-muted-foreground'}`}
                      >
                        {ATTACHMENT_TYPE_LABELS[a.attachment_type] ?? a.attachment_type}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground text-right">
                    {formatFileSize(a.size_bytes)}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${FILE_STATUS_COLORS[a.status] ?? 'bg-muted text-muted-foreground'}`}
                    >
                      {FILE_STATUS_LABELS[a.status] ?? a.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {a.download_url && (
                      <a
                        href={a.download_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-primary hover:underline mr-3"
                      >
                        Скачать
                      </a>
                    )}
                    {canWrite && isMutable && (
                      <button
                        onClick={() => handleDetach(a.file_id, a.name)}
                        className="text-xs text-destructive hover:underline"
                      >
                        Открепить
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function LastPaymentsSection({ payments, contractId }: { payments: LastPayment[]; contractId: string }) {
  if (payments.length === 0) {
    return (
      <div className="mt-6">
        <h2 className="mb-3 text-sm font-semibold">Последние платежи</h2>
        <p className="text-xs text-muted-foreground">Проведённых платежей нет</p>
      </div>
    )
  }

  return (
    <div className="mt-6">
      <h2 className="mb-3 text-sm font-semibold">Последние платежи</h2>
      <div className="rounded-md border border-border overflow-hidden">
        <table className="w-full">
          <tbody>
            {payments.map((p) => (
              <tr key={p.transaction_id} className="border-b border-border last:border-0 hover:bg-muted/20">
                <td className="px-3 py-2 text-xs text-muted-foreground font-mono">
                  {p.value_date
                    ? new Date(p.value_date).toLocaleDateString('ru-RU')
                    : '—'}
                </td>
                <td className="px-3 py-2 text-sm text-right font-medium tabular-nums">
                  +{formatAmount(p.amount)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-right">
        <Link
          href={`/dashboard/finance?contract_id=${contractId}`}
          className="text-xs text-primary hover:underline"
        >
          Все платежи →
        </Link>
      </div>
    </div>
  )
}

export default function ContractDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const canWrite = useCan('contracts.write')
  const accessToken = useAuthStore((s) => s.accessToken)
  const [pdfLoading, setPdfLoading] = useState(false)

  const { data, isLoading, isError, error } = useContract(id)
  const { data: attachments } = useContractAttachments(id)
  const activateMutation = useActivateContract()
  const closeMutation = useCloseContract()

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Загрузка...</div>
  }

  if (isError) {
    const is404 = error.message === 'NOT_FOUND'
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">
          {is404 ? 'Договор не найден' : 'Ошибка загрузки данных'}
        </p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => router.back()}>
          ← Назад
        </Button>
      </div>
    )
  }

  if (!data) return null

  const isDraft = data.status === 'DRAFT'
  const isActive = data.status === 'ACTIVE'
  const isMutable = isDraft || isActive

  const attachmentTypes = new Set(
    (attachments?.results ?? []).map((a) => a.attachment_type).filter(Boolean),
  )
  const closureConditions = {
    paid: data.paid_amount >= data.total_amount,
    has_signed: attachmentTypes.has('CONTRACT_SIGNED'),
    has_act: attachmentTypes.has('ACT_COMPLETED'),
  }
  const canClose = closureConditions.paid && closureConditions.has_signed && closureConditions.has_act

  const handleActivate = () => {
    if (!confirm(`Активировать договор «${data.title}»?`)) return
    activateMutation.mutate(id, {
      onError: (e) => alert(e.message),
    })
  }

  const handleClose = () => {
    if (!confirm(`Закрыть договор «${data.title}»? Это действие необратимо.`)) return
    closeMutation.mutate(id, {
      onError: (e) => alert(e.message),
    })
  }

  const handlePdf = async () => {
    setPdfLoading(true)
    try {
      await downloadPdf(`/api/v1/reports/contracts/${id}/pdf/`, `contract_${id}.pdf`, accessToken)
    } catch {
      alert('Ошибка при формировании PDF')
    } finally {
      setPdfLoading(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6 flex items-start gap-4">
        <Link href="/dashboard/contracts" className="text-sm text-muted-foreground hover:text-foreground mt-1">
          ← Договоры
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-xl font-semibold truncate">{data.title}</h1>
            <span
              className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[data.status] ?? 'bg-muted text-muted-foreground'}`}
            >
              {CONTRACT_STATUS_LABELS[data.status] ?? data.status}
            </span>
          </div>
          <p className="text-xs text-muted-foreground font-mono">{data.contract_number}</p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button variant="outline" size="sm" loading={pdfLoading} onClick={handlePdf}>
            PDF
          </Button>
          {canWrite && isMutable && (
            <>
              {isDraft && (
                <>
                  <Link href={`/dashboard/contracts/${id}/edit`}>
                    <Button variant="outline" size="sm">Изменить</Button>
                  </Link>
                  <Button
                    size="sm"
                    loading={activateMutation.isPending}
                    onClick={handleActivate}
                  >
                    Активировать
                  </Button>
                </>
              )}
              {isActive && (
                <div className="flex flex-col items-end gap-1">
                  <Button
                    variant="destructive"
                    size="sm"
                    loading={closeMutation.isPending}
                    disabled={!canClose}
                    title={!canClose ? 'Не выполнены условия закрытия' : undefined}
                    onClick={handleClose}
                  >
                    Закрыть
                  </Button>
                  <ul className="text-xs space-y-0.5 text-right">
                    <li className={closureConditions.paid ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground'}>
                      {closureConditions.paid ? '✓' : '○'} Оплата завершена
                    </li>
                    <li className={closureConditions.has_signed ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground'}>
                      {closureConditions.has_signed ? '✓' : '○'} Подписанный договор
                    </li>
                    <li className={closureConditions.has_act ? 'text-green-600 dark:text-green-400' : 'text-muted-foreground'}>
                      {closureConditions.has_act ? '✓' : '○'} Акт выполненных работ
                    </li>
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <dl className="divide-y divide-border rounded-md border border-border">
        {data.contragent_id && (
          <div className="flex px-4 py-3 gap-4">
            <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">Контрагент</dt>
            <dd className="text-sm flex-1"><ContragentLink contragentId={data.contragent_id} /></dd>
          </div>
        )}
        <Row label="Тип ресурса" value={RESOURCE_TYPE_LABELS[data.resource_type] ?? data.resource_type} />
        {data.contract_date && (
          <Row
            label="Дата договора"
            value={new Intl.DateTimeFormat('ru-RU').format(new Date(data.contract_date))}
          />
        )}
        <Row label="Сумма договора" value={formatAmount(data.total_amount)} />
        <Row label="Оплачено" value={formatAmount(data.paid_amount)} />
        <Row label="Остаток" value={formatAmount(data.remaining_amount)} />
        <Row
          label="Дата создания"
          value={data.created_at ? new Date(data.created_at).toLocaleString('ru-RU') : '—'}
        />
        <Row label="ID" value={data.id} mono />
      </dl>

      <LastPaymentsSection payments={data.last_payments ?? []} contractId={id} />
      <AttachmentsSection
        contractId={id}
        contractStatus={data.status}
        canWrite={canWrite}
        filesAvailable={data.files_available ?? 0}
        filesQuarantined={data.files_quarantined ?? 0}
      />
      <EventTimeline contractId={id} />
      <ObjectsSection contractId={id} />
    </div>
  )
}
