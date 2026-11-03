// React Flow + dagre helpers (mission-control graph layout pattern).
import dagre from '@dagrejs/dagre';
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

export function layout<N extends Node>(nodes: N[], edges: Edge[], dir: 'LR' | 'TB' = 'LR', size = { w: 170, h: 54 }): N[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: dir, nodesep: 18, ranksep: 46 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: (n.width as number) ?? size.w, height: (n.height as number) ?? size.h }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const p = g.node(n.id);
    const w = (n.width as number) ?? size.w;
    const h = (n.height as number) ?? size.h;
    return { ...n, position: { x: p.x - w / 2, y: p.y - h / 2 } };
  });
}

export const arrow = { type: MarkerType.ArrowClosed, width: 14, height: 14 } as const;

export function FlowCanvas({ nodes, edges, nodeTypes, label, height = 420, onNodeClick }: {
  nodes: Node[]; edges: Edge[]; nodeTypes?: Record<string, React.ComponentType<any>>; label: string; height?: number;
  onNodeClick?: (id: string) => void;
}) {
  return (
    <div style={{ height }} className="rounded-lg border bg-surface-2" role="img" aria-label={label}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView nodesDraggable={false} nodesConnectable={false}
        elementsSelectable={!!onNodeClick} onNodeClick={onNodeClick ? (_, n) => onNodeClick(n.id) : undefined}
        proOptions={{ hideAttribution: true }} minZoom={0.3}>
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
