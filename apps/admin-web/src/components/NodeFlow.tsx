import type { ReplyNode } from "../api/types";
import { StatusBadge } from "./StatusBadge";

type Props = {
  nodes: ReplyNode[];
};

export function NodeFlow({ nodes }: Props) {
  return (
    <div className="node-flow">
      {nodes.map((node, index) => (
        <div className="node-pair" key={node.node_key}>
          <div className={`flow-node flow-node-${node.status}`}>
            <div className="flow-node-title">{node.title}</div>
            <StatusBadge status={node.status} />
          </div>
          {index < nodes.length - 1 ? <div className="node-edge" /> : null}
        </div>
      ))}
    </div>
  );
}
