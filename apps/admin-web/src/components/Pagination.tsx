type Props = {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
};

export function Pagination({ total, limit, offset, onOffsetChange }: Props) {
  const currentPage = Math.floor(offset / limit) + 1;
  const pageCount = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="pagination">
      <button
        type="button"
        disabled={offset <= 0}
        onClick={() => onOffsetChange(Math.max(0, offset - limit))}
      >
        上一页
      </button>
      <span>
        第 {currentPage} / {pageCount} 页，共 {total} 条
      </span>
      <button
        type="button"
        disabled={offset + limit >= total}
        onClick={() => onOffsetChange(offset + limit)}
      >
        下一页
      </button>
    </div>
  );
}
