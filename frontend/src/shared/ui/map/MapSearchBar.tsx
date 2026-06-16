'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useMapSearch } from '@/shared/api/map'
import type { MapAddressResult, MapCadastreResult, MapObjectResult } from '@/shared/api/map'

export type SearchResult =
  | { type: 'object'; data: MapObjectResult }
  | { type: 'address'; data: MapAddressResult }
  | { type: 'cadastre'; data: MapCadastreResult }

interface MapSearchBarProps {
  onResult: (result: SearchResult) => void
  className?: string
}

function useDebounce(value: string, delay: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export function MapSearchBar({ onResult, className }: MapSearchBarProps) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const debouncedQuery = useDebounce(query, 350)

  const { data, isFetching } = useMapSearch(debouncedQuery)

  const hasResults =
    data && (data.objects.length > 0 || data.addresses.length > 0 || data.cadastre.length > 0)

  useEffect(() => {
    if (debouncedQuery.trim().length >= 3) setOpen(true)
    else setOpen(false)
  }, [debouncedQuery, data])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setOpen(false)
      setQuery('')
    }
  }, [])

  const handleSelect = useCallback(
    (result: SearchResult) => {
      setOpen(false)
      setQuery('')
      onResult(result)
    },
    [onResult],
  )

  return (
    <div ref={containerRef} className={`relative ${className ?? ''}`}>
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Адрес, кадастровый номер или объект..."
          className="text-xs border border-input rounded-md pl-2 pr-7 py-1 bg-background w-64 focus:outline-none focus:ring-1 focus:ring-ring"
        />
        {isFetching && (
          <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground animate-pulse">
            …
          </span>
        )}
      </div>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-80 bg-popover border border-border rounded-md shadow-md z-50 overflow-hidden">
          {!hasResults && !isFetching && (
            <div className="px-3 py-2 text-xs text-muted-foreground">Ничего не найдено</div>
          )}

          {data && data.objects.length > 0 && (
            <section>
              <div className="px-3 py-1 text-xs font-medium text-muted-foreground bg-muted/40">
                Объекты ERP
              </div>
              {data.objects.map((obj) => (
                <button
                  key={obj.id}
                  type="button"
                  onClick={() => handleSelect({ type: 'object', data: obj })}
                  className="w-full text-left px-3 py-1.5 text-xs hover:bg-accent flex items-start gap-2"
                >
                  <span className="mt-0.5 shrink-0">📦</span>
                  <span>
                    <span className="font-medium">{obj.name}</span>
                    {obj.address && (
                      <span className="block text-muted-foreground truncate">{obj.address}</span>
                    )}
                  </span>
                </button>
              ))}
            </section>
          )}

          {data && data.addresses.length > 0 && (
            <section>
              <div className="px-3 py-1 text-xs font-medium text-muted-foreground bg-muted/40">
                Адреса
              </div>
              {data.addresses.map((addr, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => handleSelect({ type: 'address', data: addr })}
                  className="w-full text-left px-3 py-1.5 text-xs hover:bg-accent flex items-center gap-2"
                >
                  <span className="shrink-0">📍</span>
                  <span className="truncate">{addr.label}</span>
                </button>
              ))}
            </section>
          )}

          {data && data.cadastre.length > 0 && (
            <section>
              <div className="px-3 py-1 text-xs font-medium text-muted-foreground bg-muted/40">
                Кадастровые участки
              </div>
              {data.cadastre.map((cad, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => handleSelect({ type: 'cadastre', data: cad })}
                  className="w-full text-left px-3 py-1.5 text-xs hover:bg-accent flex items-center gap-2"
                >
                  <span className="shrink-0">🗺️</span>
                  <span className="truncate font-mono">{cad.label}</span>
                </button>
              ))}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
