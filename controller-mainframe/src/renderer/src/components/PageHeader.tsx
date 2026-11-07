import React from 'react'

type Props = { title: string; sub?: React.ReactNode; children?: React.ReactNode }

// The bar across the top of a page: title, a short note, and the page's actions on the right.
const PageHeader = ({ title, sub, children }: Props) => (
  <header className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-8 py-2.5">
    <h1 className="text-[15px] font-semibold">{title}</h1>
    {sub && <span className="text-[12.5px] text-muted">{sub}</span>}
    <span className="flex-1" />
    {children}
  </header>
)

export default PageHeader
