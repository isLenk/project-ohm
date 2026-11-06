import React from 'react'

type Props = { title: string; sub?: React.ReactNode; children?: React.ReactNode }

const PageHeader = ({ title, sub, children }: Props) => (
  <header className="mb-7 flex flex-wrap items-center justify-between gap-4">
    <div>
      <h1 className="font-display text-3xl">{title}</h1>
      {sub && <p className="text-sm text-muted">{sub}</p>}
    </div>
    {children}
  </header>
)

export default PageHeader
