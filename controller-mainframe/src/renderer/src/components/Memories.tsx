import PageHeader from './PageHeader'

const Memories = () => (
  <>
    <PageHeader title="Memories" sub="Facts and past conversations are stored in Qdrant." />
    <a
      className="btn bg-accent text-ink"
      href="http://localhost:6333/dashboard#/collections"
      target="_blank"
      rel="noopener noreferrer"
    >
      Open Qdrant dashboard
    </a>
  </>
)

export default Memories
