import { useRef, useState, type ReactNode, type UIEvent } from "react";

export interface VirtualListProps {
  ariaLabel: string;
  className?: string;
  height: number;
  itemCount: number;
  overscan?: number;
  renderItem: (index: number) => ReactNode;
  rowHeight: number;
}

export function VirtualList({ ariaLabel, className = "", height, itemCount, overscan = 8, renderItem, rowHeight }: VirtualListProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const visibleCount = Math.ceil(height / rowHeight) + overscan * 2;
  const end = Math.min(itemCount, start + visibleCount);
  const visible = Array.from({ length: Math.max(0, end - start) }, (_, index) => start + index);

  function scroll(event: UIEvent<HTMLDivElement>) {
    setScrollTop(event.currentTarget.scrollTop);
  }

  return <div className={`virtual-list ${className}`} ref={viewport} role="list" aria-label={ariaLabel} onScroll={scroll} style={{ height, overflow: "auto" }}>
    <div className="virtual-list-spacer" style={{ height: itemCount * rowHeight, position: "relative" }}>
      {visible.map((index) => <div aria-posinset={index + 1} aria-setsize={itemCount} className="virtual-row" key={index} role="listitem" style={{ height: rowHeight, position: "absolute", top: index * rowHeight, width: "100%" }}>{renderItem(index)}</div>)}
    </div>
  </div>;
}
