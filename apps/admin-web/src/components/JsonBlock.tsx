type Props = {
  value: unknown;
};

export function JsonBlock({ value }: Props) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}
