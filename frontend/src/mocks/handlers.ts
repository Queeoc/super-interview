import { http, HttpResponse } from 'msw';

export const handlers = [
  http.get('/api/health', () =>
    HttpResponse.json({
      code: 200,
      message: 'success',
      data: {
        service: 'super-interview',
        version: '1.0.0',
        environment: 'development',
        status: 'healthy',
        dependencies: {}
      }
    })
  )
];
