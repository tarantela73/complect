'use client'

import { useState, useRef, useEffect, useCallback } from 'react'

export interface AsyncSelectOption {
  value: string
  label: string
}

interface AsyncSelectProps {
  placeholder?: string
  onSearch: (query: string) => Promise<AsyncSelectOption[]>
  onSelect: (option: AsyncSelectOption | null) => void
  value?: AsyncSelectOption | null
  disabled?: boolean
  minChars?: number
}

export function AsyncSelect({
  placeholder = 'Поиск...',
  onSearch,
  onSelect,
  value,
  disabled,
  minChars = 0,
}: AsyncSelectProps) {
  const [inputValue, setInputValue] = useState(value?.label ?? '')
  const [options, setOptions] = useState<AsyncSelectOption[]>([])
  const [isOpen, setIsOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setInputValue(value?.label ?? '')
  }, [value])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const search = useCallback(
    async (query: string) => {
      if (query.length < minChars) {
        setOptions([])
        setIsOpen(false)
        return
      }
      setIsLoading(true)
      try {
        const results = await onSearch(query)
        setOptions(results)
        setIsOpen(true)
      } finally {
        setIsLoading(false)
      }
    },
    [onSearch, minChars],
  )

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value
    setInputValue(q)
    if (!q) {
      onSelect(null)
      setOptions([])
      setIsOpen(false)
    }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => search(q), 300)
  }

  const handleSelect = (option: AsyncSelectOption) => {
    setInputValue(option.label)
    setIsOpen(false)
    onSelect(option)
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={inputValue}
        onChange={handleInputChange}
        onFocus={() => inputValue.length >= minChars && options.length > 0 && setIsOpen(true)}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
      />
      {isLoading && (
        <div className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
          ...
        </div>
      )}
      {isOpen && options.length > 0 && (
        <ul className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-md border border-border bg-background shadow-lg">
          {options.map((opt) => (
            <li
              key={opt.value}
              onMouseDown={(e) => {
                e.preventDefault()
                handleSelect(opt)
              }}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-muted"
            >
              {opt.label}
            </li>
          ))}
        </ul>
      )}
      {isOpen && !isLoading && options.length === 0 && inputValue.length >= minChars && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-border bg-background p-3 text-sm text-muted-foreground shadow-lg">
          Ничего не найдено
        </div>
      )}
    </div>
  )
}
