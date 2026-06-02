export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '未提供';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date);
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = units[0];

  for (const currentUnit of units) {
    unit = currentUnit;
    if (value < 1024 || currentUnit === units[units.length - 1]) {
      break;
    }
    value /= 1024;
  }

  return `${value.toFixed(value >= 100 ? 0 : 1)} ${unit}`;
}

export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '待生成';
  }

  return `${value.toFixed(1)} 分`;
}
