import React from "react";
import {
  EuiPageTemplate,
  EuiIcon,
  EuiBadge,
  EuiFlexGroup,
  EuiFlexItem,
  EuiSpacer,
  EuiStepsHorizontal,
  EuiHeader,
  EuiHeaderSectionItem,
  EuiTitle,
} from "@elastic/eui";
import { ElasticMark, OpenSearchMark, MigrationArrow } from "./Logo";

interface AppLayoutProps {
  activePage: string;
  onNavigate: (page: string) => void;
  children: React.ReactNode;
  showProxyStep: boolean;   // true only for remote_reindex + isVpcProxy
  isVpcProxyMode: boolean;  // true for any isVpcProxy
  sourceConnected: boolean;
  targetConnected: boolean;
  methodSelected: boolean;
  proxyDeployed: boolean;
  indicesSelected: boolean;
}

/** Steps when proxy_deploy step is needed (remote_reindex + isVpcProxy) */
const WITH_PROXY_STEPS = [
  { id: "source",       title: "Source"       },
  { id: "target",       title: "Target"       },
  { id: "method",       title: "Method"       },
  { id: "proxy_deploy", title: "Deploy Proxy" },
  { id: "indices",      title: "Indices"      },
  { id: "execute",      title: "Execute"      },
] as const;

/** Standard 5-step flow */
const STANDARD_STEPS = [
  { id: "source",  title: "Source"  },
  { id: "target",  title: "Target"  },
  { id: "method",  title: "Method"  },
  { id: "indices", title: "Indices" },
  { id: "execute", title: "Execute" },
] as const;

type StepCompletion = {
  source: boolean;
  target: boolean;
  method: boolean;
  proxy_deploy: boolean;
  indices: boolean;
};

export function AppLayout({
  activePage,
  onNavigate,
  children,
  showProxyStep,
  isVpcProxyMode,
  sourceConnected,
  targetConnected,
  methodSelected,
  proxyDeployed,
  indicesSelected,
}: AppLayoutProps) {
  const steps = showProxyStep ? WITH_PROXY_STEPS : STANDARD_STEPS;
  const stepIds = steps.map((s) => s.id as string);
  const activeStepIdx = stepIds.indexOf(activePage);

  const completion: StepCompletion = {
    source: sourceConnected,
    target: targetConnected,
    method: methodSelected,
    proxy_deploy: proxyDeployed,
    indices: indicesSelected,
  };

  const stepStatuses = steps.map((step, idx) => {
    const isPast = activeStepIdx === -1 || idx < activeStepIdx;
    const isComplete = isPast && completion[step.id as keyof StepCompletion] !== false;

    let status: "complete" | "current" | "incomplete" | "disabled";
    if (idx === activeStepIdx) {
      status = "current";
    } else if (isComplete) {
      status = "complete";
    } else {
      status = "incomplete";
    }

    return { title: step.title, status, onClick: () => onNavigate(step.id) };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      {/* ── Dark header bar ─────────────────────────────────────────── */}
      <EuiHeader
        theme="dark"
        position="fixed"
        sections={[
          {
            items: [
              <EuiHeaderSectionItem key="brand">
                <EuiFlexGroup gutterSize="s" alignItems="center" responsive={false}>
                  <EuiFlexItem grow={false}>
                    <OpenSearchMark height={28} />
                  </EuiFlexItem>
                  <EuiFlexItem grow={false}>
                    <MigrationArrow height={22} />
                  </EuiFlexItem>
                  <EuiFlexItem grow={false}>
                    <ElasticMark height={28} />
                  </EuiFlexItem>
                  <EuiFlexItem grow={false}>
                    <EuiTitle size="s">
                      <h1
                        style={{
                          color: "#fff",
                          fontWeight: 700,
                          letterSpacing: "-0.02em",
                          whiteSpace: "nowrap",
                          marginLeft: 8,
                        }}
                      >
                        Migration Assistant
                      </h1>
                    </EuiTitle>
                  </EuiFlexItem>
                </EuiFlexGroup>
              </EuiHeaderSectionItem>,
            ],
          },
          {
            items: [
              <EuiHeaderSectionItem key="badges">
                <EuiFlexGroup gutterSize="s" alignItems="center" responsive={false}>
                  {sourceConnected && (
                    <EuiFlexItem grow={false}>
                      <EuiBadge color="success">
                        <EuiIcon type="check" size="s" /> Source
                      </EuiBadge>
                    </EuiFlexItem>
                  )}
                  {targetConnected && (
                    <EuiFlexItem grow={false}>
                      <EuiBadge color="success">
                        <EuiIcon type="check" size="s" /> Target
                      </EuiBadge>
                    </EuiFlexItem>
                  )}
                  {isVpcProxyMode && (
                    <EuiFlexItem grow={false}>
                      <EuiBadge color="warning">VPC Proxy</EuiBadge>
                    </EuiFlexItem>
                  )}
                  {proxyDeployed && isVpcProxyMode && (
                    <EuiFlexItem grow={false}>
                      <EuiBadge color="success">
                        <EuiIcon type="check" size="s" /> Proxy Ready
                      </EuiBadge>
                    </EuiFlexItem>
                  )}
                  <EuiFlexItem grow={false}>
                    <EuiBadge color="hollow">v1.0.0</EuiBadge>
                  </EuiFlexItem>
                </EuiFlexGroup>
              </EuiHeaderSectionItem>,
            ],
          },
        ]}
      />

      {/* ── Main content area ────────────────────────────────────────── */}
      <EuiPageTemplate restrictWidth={1200} grow style={{ paddingTop: 48 }}>
        <EuiPageTemplate.Section>
          <EuiStepsHorizontal steps={stepStatuses} />
          <EuiSpacer size="l" />
          {children}
        </EuiPageTemplate.Section>
      </EuiPageTemplate>
    </div>
  );
}
