import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

import { EmptyState } from './common';

export function DataTable<T>({ data, columns, onRowClick, empty, initialSort, rowKey, label, mobileCard }: {
  data: T[];
  columns: ColumnDef<T, any>[];
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  initialSort?: SortingState;
  rowKey?: (row: T) => string;
  label: string;
  /** Optional card renderer used below the sm breakpoint instead of the table. */
  mobileCard?: (row: T) => ReactNode;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort ?? []);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: rowKey ? (row) => rowKey(row) : undefined,
  });
  if (!data.length) return <>{empty ?? <EmptyState title="Nothing to show" />}</>;
  const rows = table.getRowModel().rows;
  return (
    <>
      {mobileCard && (
        <ul className="flex flex-col gap-2 sm:hidden" aria-label={label}>
          {rows.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                className="w-full rounded-lg border bg-surface p-3 text-left hover:bg-surface-2"
                onClick={() => onRowClick?.(row.original)}
              >
                {mobileCard(row.original)}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className={cn('overflow-x-auto', mobileCard && 'hidden sm:block')}>
        <table className="w-full border-collapse text-sm" aria-label={label}>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b">
                {hg.headers.map((h) => {
                  const sort = h.column.getIsSorted();
                  const canSort = h.column.getCanSort();
                  return (
                    <th
                      key={h.id}
                      scope="col"
                      aria-sort={sort === 'asc' ? 'ascending' : sort === 'desc' ? 'descending' : undefined}
                      className="label sticky top-0 bg-surface px-3 py-2 text-left whitespace-nowrap"
                    >
                      {h.isPlaceholder ? null : canSort ? (
                        <button type="button" className="inline-flex items-center gap-1 uppercase" onClick={h.column.getToggleSortingHandler()}>
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {sort === 'asc' ? <ArrowUp className="size-3" /> : sort === 'desc' ? <ArrowDown className="size-3" /> : <ChevronsUpDown className="size-3 opacity-50" />}
                        </button>
                      ) : (
                        flexRender(h.column.columnDef.header, h.getContext())
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                className={cn('border-b last:border-0', onRowClick && 'cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2')}
                tabIndex={onRowClick ? 0 : undefined}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                onKeyDown={onRowClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(row.original); } } : undefined}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2 align-top">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
