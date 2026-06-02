export type DependencyHealth = {
  name: string;
  status: 'healthy' | 'unhealthy' | 'disabled';
  message: string;
};

export type HealthResponse = {
  service: string;
  version: string;
  environment: string;
  status: 'healthy' | 'unhealthy';
  dependencies: Record<string, DependencyHealth>;
  error?: string;
};
