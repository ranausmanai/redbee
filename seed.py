def sort(arr):
    """Sort a list of numbers. Return a new sorted list."""
    n = len(arr)
    result = arr.copy()
    for i in range(n):
        for j in range(n - 1):
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
    return result
