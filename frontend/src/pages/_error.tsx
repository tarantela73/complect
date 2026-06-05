interface ErrorProps {
  statusCode?: number
}

export default function Error(_props: ErrorProps) {
  return null
}

Error.getInitialProps = ({ res, err }: { res?: { statusCode: number }; err?: { statusCode: number } }) => ({
  statusCode: res?.statusCode ?? err?.statusCode ?? 404,
})
