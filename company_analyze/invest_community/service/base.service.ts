/**
 * 服务层基类
 * 所有业务服务都继承此基类
 */
export abstract class BaseService {
  protected constructor() {}

  /**
   * 错误处理
   */
  protected handleError(error: unknown, message: string): never {
    console.error(`${message}:`, error);
    if (error instanceof Error) {
      throw new Error(`${message}: ${error.message}`);
    }
    throw new Error(message);
  }
}
