import { useLayoutEffect, useRef, useState, type ReactNode, type UIEvent } from "react";
import { useReadingPosition } from "../state/folioNavigation";

export interface VirtualScrollState {
  atEnd: boolean;
  scrollTop: number;
}

export interface VirtualListProps {
  ariaLabel: string;
  className?: string;
  contentWidth?: string;
  height: number;
  itemCount: number;
  onScrollState?: (state: VirtualScrollState) => void;
  overscan?: number;
  renderItem: (index: number) => ReactNode;
  rowHeight: number;
  readingKey?: string;
  scrollRevision?: number;
  scrollToIndex?: number;
}

export function VirtualList({ ariaLabel, className = "", contentWidth, height, itemCount, onScrollState, overscan = 8, readingKey, renderItem, rowHeight, scrollRevision = 0, scrollToIndex }: VirtualListProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  useReadingPosition(readingKey, viewport);
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const visibleCount = Math.ceil(height / rowHeight) + overscan * 2;
  const end = Math.min(itemCount, start + visibleCount);
  const visible = Array.from({ length: Math.max(0, end - start) }, (_, index) => start + index);

  function scroll(event: UIEvent<HTMLDivElement>) {
    const element = event.currentTarget;
    setScrollTop(element.scrollTop);
    onScrollState?.({
      atEnd: element.scrollHeight - element.scrollTop - element.clientHeight <= rowHeight,
      scrollTop: element.scrollTop
    });
  }

  useLayoutEffect(() => {
    if (scrollToIndex === undefined || !viewport.current || itemCount === 0) return;
    const index = Math.max(0, Math.min(itemCount - 1, scrollToIndex));
    const target = Math.max(0, Math.min(index * rowHeight, itemCount * rowHeight - height));
    viewport.current.scrollTop = target;
    setScrollTop(target);
  }, [height, itemCount, rowHeight, scrollRevision, scrollToIndex]);

  return <div className={`virtual-list ${className}`} ref={viewport} role="list" tabIndex={0} aria-label={ariaLabel} onScroll={scroll} style={{ height, overflow: "auto" }}>
    <div className="virtual-list-spacer" style={{ height: itemCount * rowHeight, minWidth: contentWidth, position: "relative" }}>
      {visible.map((index) => <div aria-posinset={index + 1} aria-setsize={itemCount} className="virtual-row" key={index} role="listitem" style={{ height: rowHeight, position: "absolute", top: index * rowHeight, width: "100%" }}>{renderItem(index)}</div>)}
    </div>
  </div>;
}
