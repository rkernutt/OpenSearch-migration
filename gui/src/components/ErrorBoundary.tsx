import { Component, ReactNode } from "react";
import { EuiCallOut, EuiCodeBlock } from "@elastic/eui";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <EuiCallOut title="Unexpected error" color="danger" iconType="alert">
          <EuiCodeBlock language="text" fontSize="s">
            {this.state.error.message}
          </EuiCodeBlock>
        </EuiCallOut>
      );
    }
    return this.props.children;
  }
}
