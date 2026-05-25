# コーディング規約 例文集

`.claude/rules/300-coding-rules.md` の補足資料。各規範に対する具体的なコード例。

## 副作用の外部化

### 現在時刻

```kotlin
// NG: 関数内で現在時刻を取得している
fun isExpired(token: Token): Boolean {
    val now = Instant.now()
    return token.expiresAt.isBefore(now)
}

// OK: 現在時刻を引数で受け取る
fun isExpired(token: Token, now: Instant): Boolean {
    return token.expiresAt.isBefore(now)
}
```

### 乱数

```kotlin
// NG: 関数内で乱数を生成している
fun generateCode(): String {
    val random = Random()
    return (1..6).map { random.nextInt(10) }.joinToString("")
}

// OK: Random インスタンスを引数で受け取る
fun generateCode(random: Random): String {
    return (1..6).map { random.nextInt(10) }.joinToString("")
}
```

### 外部API呼び出し

```kotlin
// NG: 関数内で直接HTTPリクエストを行っている
fun fetchUserName(userId: String): String {
    val response = httpClient.get("https://api.example.com/users/$userId")
    return response.body<UserResponse>().name
}

// OK: データ取得をインターフェースで抽象化する
fun interface UserRepository {
    fun findNameById(userId: String): String
}

fun fetchUserName(userId: String, repository: UserRepository): String {
    return repository.findNameById(userId)
}
```
